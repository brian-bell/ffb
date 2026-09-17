"""Stand-in tracker for the offline backend journey: records ``--publish`` POSTs.

Binds an ephemeral port, writes it to ``<out_dir>/port``, and stores every
``POST /api/inseason/{kind}`` body as ``<out_dir>/{kind}.json`` after checking
the bearer key. The Worker e2e test then replays those exact envelopes through
the real ``/api/inseason`` routes, so the Python envelope and the TypeScript
validator are exercised against each other without network access.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

KINDS = ("lineup", "digest", "retro", "ros")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--api-key", required=True)
    args = parser.parse_args()
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    expected_auth = f"Bearer {args.api_key}"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *values: object) -> None:  # noqa: A002
            sys.stderr.write(f"capture: {format % values}\n")

        def _json(self, status: int, body: dict) -> None:
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:  # noqa: N802
            # The publish URL carries ?league=, as the Worker's route does, so
            # the kind comes from the path alone.
            path = urlsplit(self.path).path
            kind = path.rsplit("/", 1)[-1]
            if not path.startswith("/api/inseason/") or kind not in KINDS:
                self._json(404, {"error": "not found"})
                return
            if self.headers.get("Authorization") != expected_auth:
                self._json(401, {"error": "unauthorized"})
                return
            raw = self.rfile.read(int(self.headers.get("content-length") or 0))
            try:
                envelope = json.loads(raw)
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid_json", "message": "body must be JSON"})
                return
            if envelope.get("kind") != kind:
                self._json(400, {"error": "invalid_report", "message": "kind mismatch"})
                return
            (out_dir / f"{kind}.json").write_bytes(raw)
            self._json(
                200,
                {
                    "kind": kind,
                    "season": envelope.get("season"),
                    "week": envelope.get("week"),
                    "generated_at": envelope.get("generated_at"),
                },
            )

    server = HTTPServer(("127.0.0.1", 0), Handler)
    (out_dir / "port").write_text(str(server.server_address[1]))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

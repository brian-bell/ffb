"""Color-independent CLI test helpers.

The CLI renders through rich, which emits ANSI escapes whenever it believes it
is writing to a color-capable terminal. That belief depends on the environment
the suite happens to run in, so assertions against raw ``result.output`` pass in
CI and fail locally. Strip the escapes once here instead of wording CLI messages
around them.
"""

import re

from typer.testing import CliRunner

_ANSI = re.compile(rb"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    """Return ``text`` without ANSI escape sequences."""
    return _ANSI.sub(b"", text.encode("utf-8", "replace")).decode("utf-8", "replace")


class PlainCliRunner(CliRunner):
    """A ``CliRunner`` whose results carry ANSI-free output."""

    def invoke(self, *args, **kwargs):
        result = super().invoke(*args, **kwargs)
        for attr in ("stdout_bytes", "stderr_bytes", "output_bytes"):
            captured = getattr(result, attr, None)
            if captured is not None:
                setattr(result, attr, _ANSI.sub(b"", captured))
        return result

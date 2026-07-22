"""Layering guards: the pure compute modules stay I/O-free.

``vorp``/``tiers``/``board``/``names`` are pure (dicts in, dicts/strings out) so
they're trivially testable and re-derive on a config swap with no re-ingest. They
must not reach for the network, the DB, the snapshot cache, or the filesystem —
file writing lives in the CLI, DB access in the store.
"""

from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "ffb"

# Imports that would break the "pure compute" contract for these modules.
_FORBIDDEN = ("import httpx", "import duckdb", "ffb.store", "ffb.snapshot", "ffb.ingest")


@pytest.mark.parametrize("module", ["vorp.py", "tiers.py", "board.py", "names.py", "identity.py"])
def test_pure_modules_have_no_io_imports(module):
    text = (SRC / module).read_text()
    offenders = [needle for needle in _FORBIDDEN if needle in text]
    assert offenders == [], f"{module} reaches for I/O: {offenders}"

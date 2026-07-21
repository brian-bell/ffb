"""The store is the only module that touches DuckDB."""

from pathlib import Path

from ffb.ingest import resolve_rows
from ffb.store import Store


def test_init_schema_is_idempotent(tmp_path):
    s = Store(tmp_path / "db.duckdb")
    s.init_schema()
    s.init_schema()  # must not raise
    s.close()


def test_upsert_and_read_back(seeded_store):
    rows = seeded_store.projection_rows(season=2024)
    native_ids = {r["native_id"] for r in rows}
    assert "3198" in native_ids  # Derrick Henry
    henry = next(r for r in rows if r["native_id"] == "3198")
    assert henry["full_name"] == "Derrick Henry"
    assert henry["position"] == "RB"
    assert henry["stats"]["rush_yd"] == 1575.0
    assert henry["src_pts_ppr"] == 288.0
    # No crosswalk seeded -> fallback key, matched False.
    assert henry["player_key"] == "sleeper:3198"
    assert henry["matched"] is False


def test_upsert_is_idempotent_on_primary_key(seeded_store, sample_rows):
    resolved, _ = resolve_rows(seeded_store, sample_rows, "sleeper")
    before = len(seeded_store.projection_rows(season=2024))
    seeded_store.upsert_projections(resolved)  # same rows again
    after = len(seeded_store.projection_rows(season=2024))
    assert before == after


def test_filter_by_position(seeded_store):
    rbs = seeded_store.projection_rows(season=2024, position="RB")
    assert {r["position"] for r in rbs} == {"RB"}
    assert len(rbs) == 3


def test_has_season(seeded_store):
    assert seeded_store.has_season(2024)
    assert not seeded_store.has_season(1999)


def test_position_filter_is_case_insensitive(seeded_store):
    lower = seeded_store.projection_rows(season=2024, position="rb")
    assert len(lower) == 3


def test_only_store_module_imports_duckdb():
    """Enforce the 'all DB access through one store module' contract."""
    src = Path(__file__).resolve().parents[1] / "src" / "ffb"
    offenders = []
    for path in src.rglob("*.py"):
        if path.name == "store.py":
            continue
        text = path.read_text()
        if "import duckdb" in text or "duckdb." in text:
            offenders.append(path.name)
    assert offenders == [], f"duckdb used outside store.py: {offenders}"

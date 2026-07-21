"""ADP store methods: upsert/read roundtrip, delete-mirror, crosswalk read."""


def _adp_row(key, native_id, name, pos, team, adp, matched=True):
    return {
        "player_key": key,
        "season": 2024,
        "source": "ffc",
        "native_id": native_id,
        "full_name": name,
        "position": pos,
        "team": team,
        "bye": 9,
        "adp": adp,
        "adp_high": 1,
        "adp_low": 5,
        "adp_stdev": 0.7,
        "times_drafted": 1000,
        "matched": matched,
    }


def test_upsert_and_read_back_adp(store):
    store.upsert_adp(
        [
            _adp_row("2434", "2749", "Christian McCaffrey", "RB", "SFO", 1.4),
            _adp_row(
                "ffc:9001", "9001", "San Francisco Defense", "DEF", "SFO", 118.0, matched=False
            ),
        ]
    )
    rows = store.adp_rows(2024)
    assert len(rows) == 2
    cmc = next(r for r in rows if r["player_key"] == "2434")
    assert cmc["adp"] == 1.4
    assert cmc["full_name"] == "Christian McCaffrey"
    assert cmc["position"] == "RB"
    assert cmc["bye"] == 9
    assert cmc["matched"] is True
    dst = next(r for r in rows if r["player_key"] == "ffc:9001")
    assert dst["matched"] is False


def test_upsert_adp_is_idempotent_on_primary_key(store):
    rows = [_adp_row("2434", "2749", "Christian McCaffrey", "RB", "SFO", 1.4)]
    store.upsert_adp(rows)
    store.upsert_adp(rows)
    assert len(store.adp_rows(2024)) == 1


def test_delete_adp_mirrors_source(store):
    store.upsert_adp([_adp_row("2434", "2749", "Christian McCaffrey", "RB", "SFO", 1.4)])
    store.delete_adp(2024)
    assert store.adp_rows(2024) == []


def test_crosswalk_rows_reads_the_spine(store, crosswalk_rows):
    store.upsert_crosswalk(crosswalk_rows)
    rows = store.crosswalk_rows()
    henry = next(r for r in rows if r["player_key"] == "12626")
    assert henry["full_name"] == "Derrick Henry"
    assert henry["position"] == "RB"
    assert henry["team"] == "BAL"

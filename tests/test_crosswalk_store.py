"""Store side of the crosswalk: load the spine and resolve native ids to keys."""


def test_resolve_maps_native_ids_to_player_key(store, crosswalk_rows):
    store.upsert_crosswalk(crosswalk_rows)
    # Both sources' native ids resolve to the same canonical key (Henry).
    assert store.resolve("sleeper", "3198") == "12626"
    assert store.resolve("espn", "3043078") == "12626"


def test_resolve_returns_none_on_miss(store, crosswalk_rows):
    store.upsert_crosswalk(crosswalk_rows)
    assert store.resolve("sleeper", "does-not-exist") is None


def test_resolve_on_empty_crosswalk_returns_none(store):
    assert store.resolve("sleeper", "3198") is None


def test_upsert_crosswalk_is_idempotent(store, crosswalk_rows):
    store.upsert_crosswalk(crosswalk_rows)
    store.upsert_crosswalk(crosswalk_rows)  # same rows again, must not error
    assert store.resolve("espn", "4362628") == "13971"  # Ja'Marr Chase

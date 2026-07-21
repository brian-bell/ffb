"""Name normalization + (name, position) crosswalk matching (pure)."""

from ffb.names import build_name_index, match_by_name, normalize_name


def _xrow(key, name, pos, team):
    return {"player_key": key, "full_name": name, "position": pos, "team": team}


def test_normalize_lowercases_and_collapses_whitespace():
    assert normalize_name("Derrick  Henry") == "derrick henry"
    assert normalize_name("  Justin Jefferson  ") == "justin jefferson"


def test_normalize_strips_punctuation_and_apostrophes():
    # FFC "Ja'Marr Chase" must match crosswalk "Ja'Marr Chase" regardless of
    # how either side punctuates it.
    assert normalize_name("Ja'Marr Chase") == "jamarr chase"
    assert normalize_name("D'Andre Swift") == "dandre swift"
    assert normalize_name("Amon-Ra St. Brown") == "amonra st brown"


def test_normalize_strips_diacritics():
    assert normalize_name("San Francisco Défense") == "san francisco defense"


def test_normalize_strips_generational_suffixes():
    # Suffixes vary across sources ("Jr"/"Jr."/absent); drop them so they match.
    assert normalize_name("Odell Beckham Jr.") == "odell beckham"
    assert normalize_name("Michael Pittman Jr") == "michael pittman"
    assert normalize_name("Ken Walker III") == "ken walker"
    assert normalize_name("Marvin Harrison IV") == "marvin harrison"
    # A real word that merely ends in a suffix-like token must survive: only a
    # trailing standalone suffix token is dropped.
    assert normalize_name("Equanimeous St. Brown") == "equanimeous st brown"


def test_match_unique_candidate_hits():
    index = build_name_index([_xrow("2434", "Christian McCaffrey", "RB", "SFO")])
    assert match_by_name(index, "Christian McCaffrey", "RB") == "2434"


def test_match_missing_returns_none():
    index = build_name_index([_xrow("2434", "Christian McCaffrey", "RB", "SFO")])
    assert match_by_name(index, "Nobody Here", "RB") is None
    # Right name, wrong position -> no match (positions must agree).
    assert match_by_name(index, "Christian McCaffrey", "WR") is None


def test_match_drops_fa_duplicates_then_hits():
    # A retired duplicate on team "FA" must not block the active player.
    index = build_name_index(
        [
            _xrow("old", "Mike Williams", "WR", "FA"),
            _xrow("active", "Mike Williams", "WR", "NYJ"),
        ]
    )
    assert match_by_name(index, "Mike Williams", "WR") == "active"


def test_match_team_tiebreak_resolves_two_active():
    # Two active same-name players -> only the team tiebreak disambiguates.
    index = build_name_index(
        [
            _xrow("jets", "Mike Williams", "WR", "NYJ"),
            _xrow("chargers", "Mike Williams", "WR", "LAC"),
        ]
    )
    assert match_by_name(index, "Mike Williams", "WR", team="LAC") == "chargers"


def test_match_ambiguous_returns_none():
    # Two active, no usable team tiebreak -> unmatched, never a guess.
    index = build_name_index(
        [
            _xrow("jets", "Mike Williams", "WR", "NYJ"),
            _xrow("chargers", "Mike Williams", "WR", "LAC"),
        ]
    )
    assert match_by_name(index, "Mike Williams", "WR") is None
    # Team given but matching neither candidate -> still ambiguous.
    assert match_by_name(index, "Mike Williams", "WR", team="DAL") is None

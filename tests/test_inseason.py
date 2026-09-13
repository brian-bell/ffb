"""Pure publish-envelope builder for the in-season command center."""

import json

import pytest

from ffb.inseason import KINDS, build_envelope, publish_path, utc_now


def _ctx(kind):
    return {
        "lineup": {
            "league_synced_at": "2026-09-20T12:22:00Z",
            "projection_sources": ["sleeper", "espn"],
            "snapshot_generated_at": None,
        },
        "digest": {"sources": ["news", "injuries"]},
        "retro": {"actuals_synced_at": "2026-09-16T10:30:00Z"},
        "ros": {"projection_sources": ["sleeper"], "playoff_weeks_requested": [15, 16, 17]},
    }[kind]


@pytest.mark.parametrize("kind", KINDS)
def test_envelope_is_closed_and_passes_the_report_through(kind):
    report = {"anything": [1, 2, 3], "nested": {"llm": {"error": None}}}
    envelope = build_envelope(
        kind,
        season=2026,
        week=2,
        generated_at="2026-09-20T13:05:12Z",
        team_name="Bell Curve",
        context=_ctx(kind),
        report=report,
    )
    assert set(envelope) == {
        "schema_version",
        "kind",
        "season",
        "week",
        "generated_at",
        "team_name",
        "context",
        "report",
    }
    assert envelope["schema_version"] == 1
    assert envelope["kind"] == kind
    assert envelope["report"] is report
    assert envelope["context"] == _ctx(kind)
    assert json.loads(json.dumps(envelope))["week"] == 2
    assert publish_path(kind) == f"/api/inseason/{kind}"


def test_team_name_may_be_null():
    envelope = build_envelope(
        "digest",
        season=2026,
        week=1,
        generated_at="2026-09-20T13:05:12Z",
        team_name=None,
        context=_ctx("digest"),
        report={},
    )
    assert envelope["team_name"] is None


def test_context_keys_are_closed_per_kind():
    with pytest.raises(ValueError, match="missing=\\['snapshot_generated_at'\\]"):
        build_envelope(
            "lineup",
            season=2026,
            week=1,
            generated_at="2026-09-20T13:05:12Z",
            team_name="x",
            context={"league_synced_at": None, "projection_sources": []},
            report={},
        )
    with pytest.raises(ValueError, match="extra=\\['bogus'\\]"):
        build_envelope(
            "ros",
            season=2026,
            week=1,
            generated_at="2026-09-20T13:05:12Z",
            team_name="x",
            context={**_ctx("ros"), "bogus": 1},
            report={},
        )


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"kind": "waivers"}, "unknown report kind"),
        ({"season": 0}, "season must be"),
        ({"week": 0}, "week must be"),
        ({"week": True}, "week must be"),
        ({"generated_at": "2026-09-20T13:05:12+00:00"}, "generated_at must be"),
        ({"team_name": 7}, "team_name must be"),
        ({"report": []}, "report must be"),
    ],
)
def test_envelope_rejects_bad_fields(kwargs, message):
    base = dict(
        kind="retro",
        season=2026,
        week=1,
        generated_at="2026-09-20T13:05:12Z",
        team_name="x",
        context=_ctx("retro"),
        report={},
    )
    base.update(kwargs)
    kind = base.pop("kind")
    with pytest.raises(ValueError, match=message):
        build_envelope(kind, **base)


def test_publish_path_rejects_unknown_kind():
    with pytest.raises(ValueError):
        publish_path("board")


def test_utc_now_is_second_precision_zulu():
    value = utc_now()
    assert value.endswith("Z")
    assert len(value) == len("2026-09-20T13:05:12Z")

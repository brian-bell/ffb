"""``ffb`` command line — the display end of the spine."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.table import Table

from ffb import board as board_mod
from ffb import config, paths
from ffb import ros as ros_mod
from ffb.actuals import parse_actuals
from ffb.consensus import consensus_rows
from ffb.league import FixtureLeagueSource
from ffb.league_context import load_league_context
from ffb.lineup import (
    attach_injuries,
    attach_weekly_points,
    compare_lineup,
    injury_badge,
)
from ffb.retro import (
    actuals_snapshot_key,
    build_lineup_snapshot,
    lineup_snapshot_key,
    parse_lineup_snapshot,
    retro_report,
    snapshot_now,
)
from ffb.season_data import SeasonDataService
from ffb.snapshot import SnapshotCache, SnapshotPolicy
from ffb.sources import yahoo
from ffb.store import SchemaMismatchError, Store
from ffb.yahoo_auth import YahooAuthError

app = typer.Typer(help="Fantasy football pipeline (walking skeleton).", no_args_is_help=True)
league_app = typer.Typer(help="Sync and inspect stored league state.", no_args_is_help=True)
season_app = typer.Typer(help="Synchronize and inspect season datasets.", no_args_is_help=True)
board_app = typer.Typer(help="Show or export the persisted draft board.", no_args_is_help=True)
app.add_typer(league_app, name="league")
app.add_typer(season_app, name="season")
app.add_typer(board_app, name="board")
console = Console()

# Per-source columns shown by --show-sources, in display order.
_SOURCE_COLUMNS = ("sleeper", "espn")


@app.callback()
def main() -> None:
    """Keep subcommand names (e.g. ``ffb rankings``) even with one command."""


def _open_store() -> Store:
    """Open the persisted store, reporting a stale schema as a directed error."""
    store = Store(paths.db_path())
    try:
        store.init_schema()
    except SchemaMismatchError as exc:
        store.close()
        console.print(f"[red]Stored database is out of date:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    return store


@league_app.command("sync")
def league_sync(  # noqa: B008
    season: int = typer.Argument(config.DEFAULT_SEASON, help="League season."),
    fixture: Path | None = typer.Option(  # noqa: B008
        None, "--fixture", help="Offline LeagueBundle JSON fixture."
    ),
    refresh: bool = typer.Option(
        False, "--refresh", help="Refetch live Yahoo data, replacing snapshots."
    ),
) -> None:
    """Validate and atomically import live Yahoo or fixture-backed league state."""
    if fixture is not None:
        source: object = FixtureLeagueSource(fixture)
    else:
        try:
            source = yahoo.league_source_from_env(SnapshotCache(paths.snapshot_dir()))
        except YahooAuthError as exc:
            console.print(f"[red]Live Yahoo sync unavailable:[/red] {exc}")
            raise typer.Exit(code=2) from exc
    label = "fixture/mock" if fixture is not None else "live Yahoo"
    try:
        bundle = source.fetch(season, refresh=refresh)
    except ValueError as exc:
        noun = "fixture" if fixture is not None else "state"
        console.print(f"[red]League {noun} rejected:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except YahooAuthError as exc:
        console.print(f"[red]Yahoo authentication failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    except httpx.HTTPError as exc:
        console.print(f"[red]Yahoo fetch failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    store = _open_store()
    result = store.replace_league_state(bundle)
    store.close()
    console.print(
        f"[green]Synced {label} league state:[/green] {result['teams']} team(s), "
        f"{result['players']} roster player(s), {result['matched']} matched, "
        f"{result['unmatched']} unmatched."
    )


@league_app.command("show")
def league_show(
    season: int = typer.Argument(config.DEFAULT_SEASON, help="League season."),
    rosters: bool = typer.Option(False, "--rosters", help="Expand current-week roster players."),
) -> None:
    """Display persisted league source state without network access."""
    store = _open_store()
    context = store.league_context(season)
    if context is None:
        store.close()
        console.print(
            f"[yellow]No league state for {season}. Run: ffb league sync "
            f"{season} --fixture PATH[/yellow]"
        )
        raise typer.Exit(code=1)
    heading = "Live Yahoo settings" if context["source"] == "yahoo" else "Mock fixture settings"
    console.print(
        f"[yellow]{heading}[/yellow] — {context['name']} "
        f"({context['source']} synced {context['synced_at']})"
    )
    console.print(f"Week {context['current_week']} · {context['num_teams']} team(s)")
    console.print(
        "Scoring rules: "
        + ", ".join(f"{r['provider_name']} ({r['points']})" for r in context["scoring_rules"])
    )
    if context["unmapped_scoring_rules"]:
        console.print(
            "Unmapped scoring rules: "
            + ", ".join(
                f"{r['provider_name']} ({r['points']})" for r in context["unmapped_scoring_rules"]
            )
        )
    console.print(
        "Roster slots: "
        + ", ".join(f"{r['position']} × {r['count']}" for r in context["roster_slots"])
    )
    teams = store.league_teams(season)
    roster_rows = store.league_roster_rows(season)
    for team in teams:
        count = sum(r["team_key"] == team["team_key"] for r in roster_rows)
        managers = ", ".join(team["managers"]) or "no manager listed"
        console.print(f"{team['name']} ({managers}) — {count} roster player(s)")
    if rosters:
        for row in roster_rows:
            console.print(f"{row['team_key']}: {row['full_name']} ({row['primary_position']})")
    store.close()


def _service(store: Store) -> SeasonDataService:
    return SeasonDataService(store, SnapshotCache(paths.snapshot_dir()))


@contextmanager
def _verbose_sync_logging(enabled: bool) -> Iterator[None]:
    """Temporarily show INFO progress from the ffb package for one sync."""
    if not enabled:
        yield
        return

    logger = logging.getLogger("ffb")
    previous_level = logger.level
    previous_propagate = logger.propagate
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        yield
    finally:
        logger.removeHandler(handler)
        handler.close()
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate


@season_app.command("sync")
def season_sync(  # noqa: B008
    season: int = typer.Argument(config.DEFAULT_SEASON, help="Data season."),
    source: list[str] | None = typer.Option(  # noqa: B008
        None,
        "--source",
        help="all, projections, adp, sleeper, espn, ffc, schedule, or injuries; repeatable.",
    ),
    missing_only: bool = typer.Option(
        False, "--missing-only", help="Fetch only missing snapshots."
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Fetch every selected snapshot."),
    offline: bool = typer.Option(False, "--offline", help="Prohibit network access."),
    rebuild: bool = typer.Option(False, "--rebuild", help="Reprocess cached data."),
    verbose: bool = typer.Option(
        False, "-v", "--verbose", help="Log API calls and processing steps."
    ),
    week: int | None = typer.Option(
        None,
        "--week",
        help="Also ingest weekly Sleeper/ESPN projections for this week.",
    ),
) -> None:
    """Synchronize selected season datasets and record each outcome."""
    selected_policies = sum((missing_only, refresh, offline))
    if selected_policies > 1:
        raise typer.BadParameter("choose only one of --missing-only, --refresh, or --offline")
    if offline and refresh:
        raise typer.BadParameter("--offline and --refresh cannot be combined")
    if week is not None and week < 1:
        raise typer.BadParameter("week must be a positive integer")
    policy = (
        SnapshotPolicy.REFRESH
        if refresh
        else SnapshotPolicy.OFFLINE
        if offline
        else SnapshotPolicy.MISSING_ONLY
    )
    store = _open_store()
    try:
        with _verbose_sync_logging(verbose):
            results = _service(store).sync(
                season, selectors=source, policy=policy, rebuild=rebuild, week=week
            )
    except ValueError as exc:
        store.close()
        raise typer.BadParameter(str(exc)) from exc
    store.close()
    failed = False
    for result in results:
        if result.state == "ready":
            console.print(
                f"[green]ready[/green] {result.source}: {result.rows} row(s), "
                f"{result.matched} matched"
            )
        else:
            failed = True
            console.print(f"[red]failed[/red] {result.source}: {result.error}")
    if failed:
        raise typer.Exit(code=1)


@season_app.command("status")
def season_status(
    season: int = typer.Argument(config.DEFAULT_SEASON, help="Data season."),
    as_json: bool = typer.Option(False, "--json", help="Emit versioned JSON."),
) -> None:
    """Report persisted source and league state without network access."""
    store = _open_store()
    status = _service(store).status(season)
    store.close()
    if as_json:
        console.print_json(data=status)
        return
    completeness = (
        "[green]complete[/green]" if status["complete"] else "[yellow]incomplete[/yellow]"
    )
    console.print(f"{season} season data: {completeness}")
    table = Table()
    table.add_column("Source")
    table.add_column("Kind")
    table.add_column("State")
    table.add_column("Rows", justify="right")
    table.add_column("Matched", justify="right")
    table.add_column("Last success")
    table.add_column("Snapshot")
    for source_status in status["sources"]:
        table.add_row(
            source_status["name"],
            source_status["kind"],
            source_status["state"],
            str(source_status["row_count"]),
            str(source_status["match_count"]),
            source_status["last_success_at"] or "—",
            source_status["snapshot"]["key"] if source_status["snapshot"] else "—",
        )
    console.print(table)
    for source_status in status["sources"]:
        if source_status["error"]:
            console.print(f"[red]{source_status['name']}:[/red] {source_status['error']}")
    console.print(f"League: {status['league']['state']}")


@season_app.command("unmatched")
def season_unmatched(
    season: int = typer.Argument(config.DEFAULT_SEASON, help="Data season."),
    source: str | None = typer.Option(
        None, "--source", help="Filter to sleeper, espn, ffc, or injuries."
    ),
) -> None:
    """List current rows that did not resolve to canonical identities."""
    store = _open_store()
    try:
        rows = _service(store).unmatched(season, source)
    except ValueError as exc:
        store.close()
        raise typer.BadParameter(str(exc)) from exc
    store.close()
    if not rows:
        scope = f" for {source}" if source else ""
        console.print(f"No unmatched rows for {season}{scope}.")
        return
    table = Table()
    table.add_column("Source")
    table.add_column("Native ID")
    table.add_column("Player Key")
    table.add_column("Name")
    table.add_column("Pos")
    table.add_column("Team")
    for row in rows:
        table.add_row(
            row["source"],
            row["native_id"],
            row["player_key"],
            row["full_name"],
            row["position"] or "—",
            row["team"] or "—",
        )
    console.print(table)


@app.command()
def lineup(
    season: int = typer.Argument(config.DEFAULT_SEASON, help="League season."),
    week: int | None = typer.Option(
        None, "--week", help="Weekly projection slice. Defaults to stored current week."
    ),
) -> None:
    """Compare the user team's stored lineup to optimal weekly starters."""
    if week is not None and week < 1:
        raise typer.BadParameter("week must be a positive integer")
    store = _open_store()
    context = store.league_context(season)
    if context is None:
        store.close()
        console.print(
            f"[yellow]No league state for {season}. Run: ffb league sync "
            f"{season} --fixture PATH[/yellow]"
        )
        raise typer.Exit(code=1)
    chosen_week = context["current_week"] if week is None else week
    teams = store.league_teams(season)
    user_teams = [team for team in teams if team["is_user_team"]]
    if len(user_teams) != 1:
        store.close()
        console.print(
            "[red]Sit/start needs exactly one team marked is_user_team in league state.[/red]"
        )
        raise typer.Exit(code=1)
    user = user_teams[0]
    store.refresh_league_roster_identities(season, chosen_week)
    roster_rows = [
        row
        for row in store.league_roster_rows(season, week=chosen_week)
        if row["team_key"] == user["team_key"]
    ]
    scope = config.projection_scope(chosen_week)
    active_sources = [
        source for source in _SOURCE_COLUMNS if store.has_season(season, source, scope)
    ]
    if not active_sources:
        store.close()
        console.print(
            f"[red]No weekly projection sources for {season} week {chosen_week}. "
            f"Run: ffb season sync {season} --week {chosen_week}[/red]"
        )
        raise typer.Exit(code=1)
    league = load_league_context(store, season)
    consensus = consensus_rows(
        store,
        season=season,
        week=chosen_week,
        sources=active_sources,
        cfg=league.scoring,
    )
    status = _service(store).status(season)
    current_week = context["current_week"]
    apply_injuries = chosen_week == current_week
    injuries = store.injury_rows(season) if apply_injuries else []
    store.close()
    _warn_source_states(status, include_adp=False, wanted={"injuries"})
    if not apply_injuries:
        console.print(
            f"[yellow]Injury flags omitted for week {chosen_week}; "
            f"Sleeper status is current-week only (week {current_week}).[/yellow]"
        )
    if not roster_rows:
        console.print(
            f"[yellow]No roster players for {user['name']} in week {chosen_week}.[/yellow]"
        )
        raise typer.Exit(code=1)
    players = attach_injuries(attach_weekly_points(roster_rows, consensus), injuries)
    report = compare_lineup(players, league.roster_slots)
    SnapshotCache(paths.snapshot_dir()).put_json(
        lineup_snapshot_key(season, chosen_week),
        build_lineup_snapshot(
            season=season,
            week=chosen_week,
            generated_at=snapshot_now(),
            team_key=user["team_key"],
            team_name=user["name"],
            roster_slots=league.roster_slots,
            players=players,
            report=report,
        ),
        mode=0o600,
    )
    _render_lineup(report, week=chosen_week, team_name=user["name"])
    _report_scoring_provenance(league)


@app.command()
def retro(
    season: int = typer.Argument(config.DEFAULT_SEASON, help="League season."),
    week: int | None = typer.Option(
        None, "--week", help="Week to score. Defaults to the actuals bundle or stored week."
    ),
    fixture: Path | None = typer.Option(  # noqa: B008
        None, "--fixture", help="WeeklyActualsBundle JSON. Stored under snapshots/actuals/."
    ),
) -> None:
    """Compare a snapshotted sit/start run to ingested weekly actuals."""
    if week is not None and week < 1:
        raise typer.BadParameter("week must be a positive integer")
    cache = SnapshotCache(paths.snapshot_dir())
    bundle = None
    if fixture is not None:
        try:
            bundle = parse_actuals(json.loads(fixture.read_text()), season=season)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            console.print(f"[red]Invalid weekly actuals fixture:[/red] {exc}")
            raise typer.Exit(code=2) from exc
        chosen_week = week if week is not None else bundle.league["week"]
        if bundle.league["week"] != chosen_week:
            console.print(
                f"[red]Actuals week {bundle.league['week']} does not match requested "
                f"week {chosen_week}.[/red]"
            )
            raise typer.Exit(code=1)
        cache.put_json(actuals_snapshot_key(season, chosen_week), bundle.data, mode=0o600)
    else:
        chosen_week = week
        if chosen_week is None:
            store = _open_store()
            context = store.league_context(season)
            store.close()
            if context is None:
                console.print(
                    f"[red]No weekly actuals for {season}. Pass --fixture PATH or POST "
                    f"a WeeklyActualsBundle to /api/actuals.[/red]"
                )
                raise typer.Exit(code=1)
            chosen_week = context["current_week"]
        key = actuals_snapshot_key(season, chosen_week)
        if not cache.has(key):
            console.print(
                f"[red]No weekly actuals for {season} week {chosen_week}. "
                f"Pass --fixture PATH or POST a WeeklyActualsBundle to /api/actuals.[/red]"
            )
            raise typer.Exit(code=1)
        try:
            bundle = parse_actuals(cache.read_json(key), season=season)
        except ValueError as exc:
            console.print(f"[red]Stored weekly actuals are invalid:[/red] {exc}")
            raise typer.Exit(code=1) from exc

    advice_key = lineup_snapshot_key(season, chosen_week)
    if not cache.has(advice_key):
        console.print(
            f"[red]No sit/start snapshot for {season} week {chosen_week}. "
            f"Run: ffb lineup {season} --week {chosen_week}[/red]"
        )
        raise typer.Exit(code=1)
    try:
        advice = parse_lineup_snapshot(cache.read_json(advice_key))
    except ValueError as exc:
        console.print(f"[red]Stored sit/start snapshot is invalid:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    report = retro_report(advice, bundle)
    _render_retro(report)


@app.command()
def ros(
    season: int = typer.Argument(config.DEFAULT_SEASON, help="Projection season."),
    pos: str = typer.Option(
        None, "-p", "--position", help="Filter by position (e.g. RB). Omit for all."
    ),
    limit: int = typer.Option(30, "--limit", help="Max ROS rows to show."),
    playoff_weeks: str | None = typer.Option(
        None,
        "--playoff-weeks",
        help="Comma-separated playoff weeks (default: 15,16,17).",
    ),
) -> None:
    """Print rest-of-season consensus, playoff slate, and bye planning."""
    try:
        weeks = ros_mod.parse_playoff_weeks(playoff_weeks)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    store = _open_store()
    active_sources = [
        source for source in _SOURCE_COLUMNS if store.has_season(season, source, "season")
    ]
    if not active_sources:
        store.close()
        console.print(f"[red]No projection sources are available for {season}.[/red]")
        raise typer.Exit(code=1)
    _warn_source_states(_service(store).status(season), include_adp=False)
    games = store.schedule_game_rows(season)
    if not games:
        console.print(
            f"[yellow]Warning: schedule games are missing for {season}; "
            f"playoff slates will be empty. Run `ffb season sync {season} "
            f"--source schedule`.[/yellow]"
        )
    league = load_league_context(store, season)
    consensus = consensus_rows(
        store,
        season=season,
        position=None,
        sources=active_sources,
        cfg=league.scoring,
    )
    byes = store.team_bye_rows(season)
    roster: list[dict] = []
    context = store.league_context(season)
    if context is not None:
        user_teams = [team for team in store.league_teams(season) if team["is_user_team"]]
        if len(user_teams) == 1:
            store.refresh_league_roster_identities(season, context["current_week"])
            roster = [
                row
                for row in store.league_roster_rows(season, week=context["current_week"])
                if row["team_key"] == user_teams[0]["team_key"]
            ]
    store.close()
    report = ros_mod.ros_report(
        consensus,
        byes=byes,
        games=games,
        playoff_weeks=weeks,
        roster=roster or None,
        position=pos,
        current_week=None if context is None else context["current_week"],
    )
    if not report["players"]:
        scope = f"position {pos}" if pos else "any position"
        console.print(f"[yellow]No rest-of-season projections for {season} ({scope}).[/yellow]")
        raise typer.Exit(code=0)
    _render_ros(report, season=season, pos=pos, limit=limit)
    _report_scoring_provenance(league)


@app.command()
def rankings(
    season: int = typer.Argument(config.DEFAULT_SEASON, help="Projection season."),
    pos: str = typer.Option(
        None, "-p", "--position", help="Filter by position (e.g. RB). Omit for all."
    ),
    limit: int = typer.Option(30, "--limit", help="Max rows to show."),
    show_sources: bool = typer.Option(
        False, "--show-sources", help="Show per-source + consensus columns."
    ),
) -> None:
    """Print rankings from persisted projections without ingesting."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    store = _open_store()
    active_sources = [
        source for source in _SOURCE_COLUMNS if store.has_season(season, source, "season")
    ]
    if not active_sources:
        store.close()
        console.print(f"[red]No projection sources are available for {season}.[/red]")
        raise typer.Exit(code=1)
    _warn_source_states(_service(store).status(season), include_adp=False)
    league = load_league_context(store, season)
    rows = consensus_rows(
        store,
        season=season,
        position=pos,
        sources=active_sources,
        cfg=league.scoring,
    )
    store.close()

    if not rows:
        scope = f"position {pos}" if pos else "any position"
        console.print(f"[yellow]No projections for {season} ({scope}).[/yellow]")
        raise typer.Exit(code=0)

    rows = rows[:limit]
    _render(rows, season=season, pos=pos, sources=show_sources)
    _report_unmatched(rows)
    _report_scoring_provenance(league)


def _validate_player_pool(player_pool: str) -> str:
    if player_pool not in board_mod.PLAYER_POOLS:
        raise typer.BadParameter(f"unsupported player pool: {player_pool}")
    return player_pool


def _load_board(season: int, player_pool: str = "draftable") -> tuple[list[dict], object, dict]:
    store = _open_store()
    active_sources = [
        source for source in _SOURCE_COLUMNS if store.has_season(season, source, "season")
    ]
    if not active_sources:
        store.close()
        console.print(f"[red]No projection sources are available for {season}.[/red]")
        raise typer.Exit(code=1)
    status = _service(store).status(season)
    _warn_source_states(status, include_adp=True)
    league = load_league_context(store, season)
    consensus = consensus_rows(
        store,
        season=season,
        position=None,
        sources=active_sources,
        cfg=league.scoring,
    )
    adp = store.adp_rows(season)
    byes = store.team_bye_rows(season)
    injuries = store.injury_rows(season)
    store.close()
    _report_player_pool(
        board_mod.pool_counts(consensus, adp, roster_slots=league.roster_slots), player_pool
    )
    return (
        board_mod.board_rows(
            consensus,
            adp,
            byes=byes,
            injuries=injuries,
            roster_slots=league.roster_slots,
            num_teams=league.num_teams,
            player_pool=player_pool,
        ),
        league,
        status,
    )


def _report_player_pool(counts: dict[str, int], player_pool: str) -> None:
    """Show how many rankable rows the selected pool keeps, and how many it drops.

    A source that stops reporting activity degrades the default ``draftable``
    pool silently otherwise: the board just gets smaller.
    """
    selected = counts[player_pool]
    excluded = counts["all"] - selected
    summary = f"Player pool {player_pool}: {selected} of {counts['all']} rankable player(s)"
    if excluded:
        summary += f" ({excluded} excluded as not draftable)"
    style = "yellow" if not selected else "dim"
    console.print(f"[{style}]{summary}.[/{style}]")


@board_app.command("show")
def board_show(
    season: int = typer.Argument(config.DEFAULT_SEASON, help="Projection season."),
    pos: str = typer.Option(None, "-p", "--position", help="Filter terminal rows."),
    limit: int = typer.Option(50, "--limit", help="Max rows to show."),
    player_pool: str = typer.Option(
        "draftable", "--player-pool", help="Player pool: draftable or all."
    ),
) -> None:
    """Show a board computed only from persisted season data."""
    board, league, _ = _load_board(season, _validate_player_pool(player_pool))
    if not board:
        console.print(f"[yellow]No board rows for {season}.[/yellow]")
        return
    shown = [r for r in board if pos is None or (r["pos"] or "").upper() == pos.upper()]
    _render_board(shown[:limit], season=season, pos=pos)
    _report_board_unmatched(shown[:limit])
    _report_scoring_provenance(league)


@board_app.command("export")
def board_export(  # noqa: B008
    season: int = typer.Argument(config.DEFAULT_SEASON, help="Projection season."),
    formats: list[str] | None = typer.Option(  # noqa: B008
        None, "--format", help="json, csv, or markdown; repeatable (default: all)."
    ),
    output_dir: Path | None = typer.Option(  # noqa: B008
        None, "--output-dir", help="Export directory."
    ),
    player_pool: str = typer.Option(
        "draftable", "--player-pool", help="Player pool: draftable or all."
    ),
) -> None:
    """Export the selected persisted board in one or more formats."""
    board, league, _ = _load_board(season, _validate_player_pool(player_pool))
    if not board:
        console.print(
            f"[red]No {player_pool} board rows for {season}; refusing to write an empty "
            f"export.[/red] Re-sync the projection sources, or use --player-pool all to "
            f"inspect the unfiltered pool."
        )
        raise typer.Exit(code=1)
    selected = formats or ["json", "csv", "markdown"]
    invalid = sorted(set(selected) - {"json", "csv", "markdown"})
    if invalid:
        raise typer.BadParameter(f"unsupported format(s): {', '.join(invalid)}")
    _export_board(
        board,
        season=season,
        out_dir=output_dir or paths.export_dir(),
        league=league,
        formats=list(dict.fromkeys(selected)),
    )


def _export_board(
    board: list[dict], *, season: int, out_dir: Path, league: object, formats: list[str]
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    written: list[Path] = []
    if "markdown" in formats:
        path = out_dir / "cheatsheet.md"
        path.write_text(board_mod.to_markdown(board, season=season))
        written.append(path)
    if "csv" in formats:
        path = out_dir / "cheatsheet.csv"
        path.write_text(board_mod.to_csv(board))
        written.append(path)
    if "json" in formats:
        doc = board_mod.to_board_json(
            board,
            season=season,
            num_teams=league.num_teams,
            roster_slots=league.roster_slots,
            generated_at=generated_at,
            scoring=league.scoring_provenance,
        )
        path = out_dir / "board.json"
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
        written.append(path)
    console.print("[green]Wrote[/green] " + ", ".join(str(path) for path in written))


def _warn_source_states(status: dict, *, include_adp: bool, wanted: set[str] | None = None) -> None:
    if wanted is None:
        wanted = {"crosswalk", "sleeper", "espn"}
        if include_adp:
            # The board consumes ADP and schedule byes; warn when either is off.
            wanted.update(("ffc", "schedule", "injuries"))
    for source in status["sources"]:
        if source["name"] not in wanted:
            continue
        if source.get("stale"):
            if source["name"] == "ffc":
                console.print(
                    "[yellow]Warning: ffc ADP was synced for a different league size; "
                    f"run `ffb season sync {status['season']} --source ffc`.[/yellow]"
                )
            elif source["name"] == "injuries":
                console.print(
                    f"[yellow]Warning: injuries has stale identity resolution; run "
                    f"`ffb season sync {status['season']} --source injuries`.[/yellow]"
                )
            else:
                console.print(
                    f"[yellow]Warning: {source['name']} has stale identity resolution; "
                    f"run `ffb season sync {status['season']} --rebuild`.[/yellow]"
                )
        if source["state"] == "ready":
            continue
        retained = (
            f"; using retained data from {source['last_success_at']}"
            if source["row_count"] and source["last_success_at"]
            else ""
        )
        error = f": {source['error']}" if source["error"] else ""
        console.print(
            f"[yellow]Warning: {source['name']} is {source['state']}{error}{retained}.[/yellow]"
        )


def _render_ros(report: dict, *, season: int, pos: str | None, limit: int) -> None:
    """Print ROS consensus, team playoff strength, and optional bye plan."""
    weeks = ",".join(str(week) for week in report["playoff_weeks"])
    title = f"{season} rest-of-season strategy" + (f" — {pos.upper()}" if pos else "")
    console.print(f"[yellow]{title}[/yellow] — playoff weeks {weeks}")
    table = Table(title=f"{season} ROS rankings")
    table.add_column("Rank", justify="right", style="cyan")
    table.add_column("Player")
    table.add_column("Pos", justify="center")
    table.add_column("Team", justify="center")
    table.add_column("Bye", justify="center", style="dim")
    table.add_column("ROS", justify="right", style="green")
    table.add_column("Playoff")
    table.add_column("Diff", justify="right")
    for row in report["players"][:limit]:
        table.add_row(
            str(row["rank"]),
            row["name"],
            row["position"] or "—",
            row["team"] or "—",
            _cell(row["bye"]),
            _num(row["ros"]),
            row["playoff"] or "—",
            _cell(row["playoff_difficulty"]),
        )
    console.print(table)
    if report["teams"]:
        strength = Table(title="Playoff schedule strength")
        strength.add_column("Diff", justify="right", style="cyan")
        strength.add_column("Team", justify="center")
        strength.add_column("Games", justify="right")
        strength.add_column("Opp DEF", justify="right")
        strength.add_column("Slate")
        for row in report["teams"]:
            strength.add_row(
                _cell(row["difficulty"]),
                row["team"],
                str(row["games"]),
                _num(row["avg_opp_def"]),
                row["matchups"] or "—",
            )
        console.print(strength)
    if report["bye_plan"]:
        console.print("[yellow]Bye-week plan[/yellow]")
        for group in report["bye_plan"]:
            names = ", ".join(_bye_plan_player_label(player) for player in group["players"])
            thin = (
                f" — thin {', '.join(group['thin_positions'])}" if group["thin_positions"] else ""
            )
            console.print(f"Week {group['bye']}: {names}{thin}")
    if not report["usage_available"]:
        console.print("[dim]Usage trends are not ingested; stash/buy-low flags omitted.[/dim]")


def _bye_plan_player_label(player: dict) -> str:
    """Show IR/IL/BN for non-starters instead of collapsing every reserve to BN."""
    position = player.get("position") or "—"
    if player.get("starter"):
        return f"{player['name']} ({position})"
    slot = player.get("selected_position") or "BN"
    return f"{player['name']} ({position}, {slot})"


def _render_lineup(report: dict, *, week: int, team_name: str) -> None:
    """Print current vs optimal starters, sit/start swaps, and close calls."""
    console.print(f"[yellow]Week {week} sit/start[/yellow] — {team_name}")
    console.print(
        f"Current {report['current_total']:.1f}   "
        f"Optimal {report['optimal_total']:.1f}   "
        f"Δ {report['delta']:+.1f}"
    )
    if report.get("injury_as_of"):
        console.print(f"Injuries as of {report['injury_as_of']}")
    table = Table(title=f"Week {week} lineup")
    table.add_column("Slot", justify="center")
    table.add_column("Current")
    table.add_column("Pts", justify="right", style="green")
    table.add_column("Optimal")
    table.add_column("Pts", justify="right", style="green")
    for pair in report["aligned"]:
        left = pair["current"]
        right = pair["optimal"]
        table.add_row(
            pair["slot"],
            _lineup_name(left) if left else "—",
            _num(left["points"]) if left else "—",
            _lineup_name(right) if right else "—",
            _num(right["points"]) if right else "—",
        )
    console.print(table)
    if report["start"] or report["sit"] or report["undecidable"]:
        for row in report["start"]:
            console.print(
                f"[green]Start[/green] {_lineup_name(row)} ({row['slot']}, {_num(row['points'])})"
            )
        for row in report["sit"]:
            console.print(
                f"[red]Sit[/red] {_lineup_name(row)} "
                f"({row['selected_position']}, {_num(row['points'])})"
            )
        for row in report["undecidable"]:
            console.print(
                f"[yellow]Undecidable[/yellow] {row['name']} ({row['slot']}; no weekly projection)"
            )
    else:
        console.print("[green]Stored lineup matches the weekly optimum.[/green]")
    for row in report["close_calls"]:
        console.print(
            f"[yellow]Close[/yellow] {row['name']} {_num(row['points'])} vs "
            f"{row['versus']} ({row['slot']}, Δ {row['delta']:.1f})"
        )
    if report["missing_projections"]:
        names = ", ".join(row["name"] for row in report["missing_projections"][:8])
        more = "…" if len(report["missing_projections"]) > 8 else ""
        console.print(
            f"[yellow]⚠ {len(report['missing_projections'])} rostered player(s) "
            f"have no weekly projection: {names}{more}[/yellow]"
        )


def _render_retro(report: dict) -> None:
    """Print recommended vs started actuals, sit/start hits, and source accuracy."""
    week = report["week"]
    console.print(f"[yellow]Week {week} retro[/yellow] — {report['team_name']}")
    console.print(
        f"Recommended {report['recommended_total']:.1f}   "
        f"Started {report['started_total']:.1f}   "
        f"Δ {report['delta']:+.1f}"
    )
    matchup = report.get("matchup")
    if matchup:
        console.print(f"Scoreboard {matchup['user_points']:.1f}–{matchup['opponent_points']:.1f}")
    if report["start_hits"] or report["start_misses"] or report["sit_hits"] or report["sit_misses"]:
        for row in report["start_hits"]:
            console.print(
                f"[green]Start hit[/green] {row['name']} "
                f"(actual {_num(row['actual'])}, proj {_num(row['projected'])})"
            )
        for row in report["start_misses"]:
            console.print(
                f"[red]Start miss[/red] {row['name']} "
                f"(actual {_num(row['actual'])}, proj {_num(row['projected'])})"
            )
        for row in report["sit_hits"]:
            console.print(
                f"[green]Sit hit[/green] {row['name']} "
                f"(actual {_num(row['actual'])}, proj {_num(row['projected'])})"
            )
        for row in report["sit_misses"]:
            console.print(
                f"[red]Sit miss[/red] {row['name']} "
                f"(actual {_num(row['actual'])}, proj {_num(row['projected'])})"
            )
    else:
        console.print("[green]No sit/start swaps in the advice snapshot.[/green]")
    if report["source_accuracy"]:
        table = Table(title=f"Week {week} source accuracy")
        table.add_column("Source")
        table.add_column("n", justify="right")
        table.add_column("MAE", justify="right")
        table.add_column("Bias", justify="right")
        for row in report["source_accuracy"]:
            table.add_row(
                row["source"],
                str(row["n"]),
                f"{row['mae']:.1f}",
                f"{row['bias']:+.1f}",
            )
        console.print(table)
    if report["missing_actuals"]:
        names = ", ".join(row["name"] for row in report["missing_actuals"][:8])
        more = "…" if len(report["missing_actuals"]) > 8 else ""
        console.print(
            f"[yellow]⚠ {len(report['missing_actuals'])} advised player(s) "
            f"have no weekly actuals: {names}{more}[/yellow]"
        )


def _render_board(rows: list[dict], *, season: int, pos: str | None) -> None:
    title = f"{season} cheat sheet" + (f" — {pos.upper()}" if pos else "")
    table = Table(title=title)
    table.add_column("Rank", justify="right", style="cyan")
    table.add_column("Tier", justify="center")
    table.add_column("Player")
    table.add_column("Pos", justify="center")
    table.add_column("Team", justify="center")
    table.add_column("Bye", justify="center", style="dim")
    table.add_column("Proj", justify="right", style="green")
    table.add_column("VORP", justify="right", style="green")
    table.add_column("ADP", justify="right")
    # "+/−" = adp_rank − rank (positive = market drafts them later than we value).
    table.add_column("+/−", justify="right")

    for row in rows:
        table.add_row(
            str(row["rank"]),
            _cell(row["tier"]),
            row["name"],
            row["pos"] or "—",
            row["team"] or "—",
            _cell(row["bye"]),
            _num(row["points"]),
            _num(row["vorp"]),
            _num(row["adp"]),
            _value_delta(row),
        )
    console.print(table)


def _cell(value: object) -> str:
    return "—" if value is None else str(value)


def _num(value: object) -> str:
    return "—" if value is None else f"{value:.1f}"


def _lineup_name(row: dict) -> str:
    name = row["name"]
    badge = injury_badge(row)
    return f"{name} ({badge})" if badge else name


def _value_delta(row: dict) -> str:
    """``adp_rank − rank``: positive means the market drafts them later than we
    value them (a value pick); ``—`` when either rank is missing."""
    if row["adp_rank"] is None:
        return "—"
    delta = row["adp_rank"] - row["rank"]
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta}"


def _report_board_unmatched(rows: list[dict]) -> None:
    """Surface unresolved identities among the shown board players."""
    misses = [r for r in rows if not r["matched"]]
    if not misses:
        return
    names = ", ".join(r["name"] for r in misses[:8])
    more = "…" if len(misses) > 8 else ""
    console.print(
        f"[yellow]⚠ {len(misses)} shown player(s) unmatched to crosswalk "
        f"(source-only): {names}{more}[/yellow]"
    )


def _render(rows: list[dict], *, season: int, pos: str | None, sources: bool) -> None:
    title = f"{season} rankings" + (f" — {pos.upper()}" if pos else "")
    table = Table(title=title)
    table.add_column("Rank", justify="right", style="cyan")
    table.add_column("Player")
    table.add_column("Pos", justify="center")
    table.add_column("Team", justify="center")
    if sources:
        for source in _SOURCE_COLUMNS:
            table.add_column(source.capitalize(), justify="right")
        table.add_column("Consensus", justify="right", style="green")
        table.add_column("n", justify="right", style="dim")
    else:
        table.add_column("Proj (League)", justify="right", style="green")

    for row in rows:
        cells = [str(row["rank"]), row["full_name"], row["position"], row["team"] or "—"]
        if sources:
            for source in _SOURCE_COLUMNS:
                pts = row["source_points"].get(source)
                cells.append(f"{pts:.1f}" if pts is not None else "—")
            cells.append(f"{row['consensus']:.1f}")
            cells.append(str(row["n"]))
        else:
            cells.append(f"{row['consensus']:.1f}")
        table.add_row(*cells)
    console.print(table)


def _report_scoring_provenance(league: object) -> None:
    """Report whether scoring came from configured or synchronized Yahoo rules."""
    if league.scoring_provenance == "configured-yahoo":
        console.print("[dim]Scored with configured Yahoo league settings.[/dim]")
    else:
        console.print("[yellow]Scored with mock fixture league settings.[/yellow]")


def _report_unmatched(rows: list[dict]) -> None:
    """Surface unresolved identities among shown players (never silently drop)."""
    misses = [r for r in rows if not r["matched"]]
    if not misses:
        return
    names = ", ".join(r["full_name"] for r in misses[:8])
    more = "…" if len(misses) > 8 else ""
    console.print(
        f"[yellow]⚠ {len(misses)} shown player(s) unmatched to crosswalk "
        f"(source-only): {names}{more}[/yellow]"
    )


if __name__ == "__main__":
    app()

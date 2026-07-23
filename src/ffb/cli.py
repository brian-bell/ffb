"""``ffb`` command line — the display end of the spine."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ffb import board as board_mod
from ffb import config, paths
from ffb.consensus import consensus_rows
from ffb.league import FixtureLeagueSource
from ffb.league_context import load_league_context
from ffb.season_data import SeasonDataService
from ffb.snapshot import SnapshotCache, SnapshotPolicy
from ffb.store import Store

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


@league_app.command("sync")
def league_sync(  # noqa: B008
    season: int = typer.Argument(config.DEFAULT_SEASON, help="League season."),
    fixture: Path | None = typer.Option(  # noqa: B008
        None, "--fixture", help="Offline LeagueBundle JSON fixture."
    ),
) -> None:
    """Validate and atomically import fixture-backed league state."""
    if fixture is None:
        console.print("[yellow]Live Yahoo sync is pending Task 2b; use --fixture PATH.[/yellow]")
        raise typer.Exit(code=2)
    try:
        bundle = FixtureLeagueSource(fixture).fetch(season)
    except ValueError as exc:
        console.print(f"[red]League fixture rejected:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    store = Store(paths.db_path())
    store.init_schema()
    result = store.replace_league_state(bundle)
    store.close()
    console.print(
        f"[green]Synced fixture/mock league state:[/green] {result['teams']} team(s), "
        f"{result['players']} roster player(s), {result['matched']} matched, "
        f"{result['unmatched']} unmatched."
    )


@league_app.command("show")
def league_show(
    season: int = typer.Argument(config.DEFAULT_SEASON, help="League season."),
    rosters: bool = typer.Option(False, "--rosters", help="Expand current-week roster players."),
) -> None:
    """Display persisted league source state without network access."""
    store = Store(paths.db_path())
    store.init_schema()
    context = store.league_context(season)
    if context is None:
        store.close()
        console.print(
            f"[yellow]No league state for {season}. Run: ffb league sync "
            f"{season} --fixture PATH[/yellow]"
        )
        raise typer.Exit(code=1)
    console.print(
        f"[yellow]Mock fixture settings[/yellow] — {context['name']} "
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


@season_app.command("sync")
def season_sync(  # noqa: B008
    season: int = typer.Argument(config.DEFAULT_SEASON, help="Data season."),
    source: list[str] | None = typer.Option(  # noqa: B008
        None, "--source", help="all, projections, adp, sleeper, espn, or ffc; repeatable."
    ),
    missing_only: bool = typer.Option(
        False, "--missing-only", help="Fetch only missing snapshots."
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Fetch every selected snapshot."),
    offline: bool = typer.Option(False, "--offline", help="Prohibit network access."),
    rebuild: bool = typer.Option(False, "--rebuild", help="Reprocess cached data."),
) -> None:
    """Synchronize selected season datasets and record each outcome."""
    selected_policies = sum((missing_only, refresh, offline))
    if selected_policies > 1:
        raise typer.BadParameter("choose only one of --missing-only, --refresh, or --offline")
    if offline and refresh:
        raise typer.BadParameter("--offline and --refresh cannot be combined")
    policy = (
        SnapshotPolicy.REFRESH
        if refresh
        else SnapshotPolicy.OFFLINE
        if offline
        else SnapshotPolicy.MISSING_ONLY
    )
    store = Store(paths.db_path())
    store.init_schema()
    try:
        results = _service(store).sync(season, selectors=source, policy=policy, rebuild=rebuild)
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
    store = Store(paths.db_path())
    store.init_schema()
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
    source: str | None = typer.Option(None, "--source", help="Filter to sleeper, espn, or ffc."),
) -> None:
    """List current rows that did not resolve to canonical identities."""
    store = Store(paths.db_path())
    store.init_schema()
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

    store = Store(paths.db_path())
    store.init_schema()
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


def _load_board(season: int) -> tuple[list[dict], object, dict]:
    store = Store(paths.db_path())
    store.init_schema()
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
    store.close()
    return (
        board_mod.board_rows(
            consensus,
            adp,
            roster_slots=league.roster_slots,
            num_teams=league.num_teams,
        ),
        league,
        status,
    )


@board_app.command("show")
def board_show(
    season: int = typer.Argument(config.DEFAULT_SEASON, help="Projection season."),
    pos: str = typer.Option(None, "-p", "--position", help="Filter terminal rows."),
    limit: int = typer.Option(50, "--limit", help="Max rows to show."),
) -> None:
    """Show a board computed only from persisted season data."""
    board, league, _ = _load_board(season)
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
) -> None:
    """Export the full persisted board in one or more formats."""
    board, league, _ = _load_board(season)
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


def _warn_source_states(status: dict, *, include_adp: bool) -> None:
    wanted = {"crosswalk", "sleeper", "espn"}
    if include_adp:
        wanted.add("ffc")
    for source in status["sources"]:
        if source["name"] not in wanted:
            continue
        if source.get("stale"):
            if source["name"] == "ffc":
                console.print(
                    "[yellow]Warning: ffc ADP was synced for a different league size; "
                    f"run `ffb season sync {status['season']} --source ffc`.[/yellow]"
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
        table.add_column("Proj (PPR)", justify="right", style="green")

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
    """Note that points come from placeholder league settings, not real Yahoo ones.

    Scoring is applied silently, so without this a run looks league-accurate when
    it is only typical full-PPR defaults. Slice 2 makes this conditional (shown
    only when falling back to the placeholder); today it always applies.
    """
    if league.scoring_provenance == "placeholder":
        console.print(
            "[dim]Scored with placeholder league settings (typical full-PPR); "
            "sync a fixture for exact mock scoring.[/dim]"
        )
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

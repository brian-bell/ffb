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
from ffb.ingest import (
    Reconciliation,
    ensure_adp_ingested,
    ensure_crosswalk,
    ensure_espn_ingested,
    ensure_ingested,
)
from ffb.league import FixtureLeagueSource
from ffb.league_context import load_league_context
from ffb.snapshot import SnapshotCache
from ffb.store import Store

app = typer.Typer(help="Fantasy football pipeline (walking skeleton).", no_args_is_help=True)
league_app = typer.Typer(help="Sync and inspect stored league state.", no_args_is_help=True)
app.add_typer(league_app, name="league")
console = Console()

# Per-source columns shown by --sources, in display order.
_SOURCE_COLUMNS = ("sleeper", "espn")


@app.callback()
def main() -> None:
    """Keep subcommand names (e.g. ``ffb rankings``) even with one command."""


@league_app.command("sync")
def league_sync(  # noqa: B008
    season: int = typer.Option(config.DEFAULT_SEASON, "--season", help="League season."),
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
    season: int = typer.Option(config.DEFAULT_SEASON, "--season", help="League season."),
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
            f"--season {season} --fixture PATH[/yellow]"
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
            console.print(f"{row['team_key']}: {row['full_name']} ({row['position']})")
    store.close()


def _ingest_sources(
    store: Store,
    cache: SnapshotCache,
    season: int,
    *,
    refresh: bool,
    with_espn: bool,
) -> list[str]:
    """Run the crosswalk + Sleeper (+ optional ESPN) ingest, returning the
    sources that actually succeeded (so consensus averages only those).

    Best-effort degradation (shared by ``rankings`` and ``cheatsheet``): a failed
    crosswalk leaves players unmatched; a failed ESPN falls back to Sleeper-only;
    only a missing Sleeper snapshot while offline is fatal.
    """
    try:
        ensure_crosswalk(store, cache, refresh=refresh)
    except Exception as exc:  # noqa: BLE001 - degrade, don't crash
        console.print(f"[yellow]Crosswalk unavailable ({exc}); players may be unmatched.[/yellow]")

    try:
        ensure_ingested(store, cache, season=season, refresh=refresh)
    except FileNotFoundError as exc:
        console.print(f"[red]No Sleeper snapshot and offline:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    active_sources = ["sleeper"]
    if with_espn:
        try:
            ensure_espn_ingested(store, cache, season=season, refresh=refresh)
            active_sources.append("espn")
        except Exception as exc:  # noqa: BLE001 - fall back to Sleeper only
            console.print(f"[yellow]ESPN unavailable ({exc}); showing Sleeper only.[/yellow]")
    return active_sources


@app.command()
def rankings(
    pos: str = typer.Option(None, "--pos", help="Filter by position (e.g. RB). Omit for all."),
    season: int = typer.Option(config.DEFAULT_SEASON, "--season", help="Projection season."),
    limit: int = typer.Option(30, "--limit", help="Max rows to show."),
    sources: bool = typer.Option(
        False, "--sources", help="Add ESPN and show per-source + consensus columns."
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Re-fetch sources (network)."),
) -> None:
    """Print consensus-ranked projections (Sleeper, plus ESPN with --sources)."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    store = Store(paths.db_path())
    store.init_schema()
    cache = SnapshotCache(paths.snapshot_dir())

    # Crosswalk is best-effort: without it players simply stay unmatched. When it
    # arrives after a source was first ingested, ingest self-heals — it re-resolves
    # any stranded rows offline (see Store.has_stale_resolution).
    active_sources = _ingest_sources(store, cache, season, refresh=refresh, with_espn=sources)

    # Consensus uses only the sources that actually succeeded this run, so a
    # failed ESPN fetch can't leave stale ESPN rows silently feeding the average,
    # and the same command stays deterministic regardless of what a prior run
    # persisted.
    # Score with THIS league's settings, not generic PPR. Placeholder config for
    # now; slice 2 swaps this one call for a store read (Yahoo settings), keeping
    # config.LEAGUE_SCORING as the offline fallback.
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
    _render(rows, season=season, pos=pos, sources=sources)
    _report_unmatched(rows)
    _report_scoring_provenance(league)


@app.command()
def cheatsheet(
    pos: str = typer.Option(None, "--pos", help="Filter the terminal board by position."),
    season: int = typer.Option(config.DEFAULT_SEASON, "--season", help="Projection season."),
    limit: int = typer.Option(50, "--limit", help="Max rows to show in the terminal."),
    refresh: bool = typer.Option(False, "--refresh", help="Re-fetch sources (network)."),
    export: bool = typer.Option(
        False, "--export", help="Write cheatsheet.md/.csv + board.json (the data contract)."
    ),
    export_dir: str = typer.Option(
        None,
        "--export-dir",
        help="Directory for --export (default: exports/, or $FFB_EXPORT_DIR).",
    ),
) -> None:
    """Draft cheat sheet: consensus + ADP + VORP + tiers, with a board.json export.

    ``--pos``/``--limit`` shape only the terminal view; ``--export`` always writes
    the full board (the slice-6 tracker's data contract).
    """
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    store = Store(paths.db_path())
    store.init_schema()
    cache = SnapshotCache(paths.snapshot_dir())

    active_sources = _ingest_sources(store, cache, season, refresh=refresh, with_espn=True)

    # ADP is best-effort like ESPN: a failed FFC pull just drops the ADP columns.
    league = load_league_context(store, season)
    adp_recon = Reconciliation(source="ffc")
    adp_failed = False
    try:
        adp_recon = ensure_adp_ingested(
            store, cache, season=season, refresh=refresh, teams=league.num_teams
        )
    except Exception as exc:  # noqa: BLE001 - degrade, render without ADP
        adp_failed = True
        console.print(f"[yellow]ADP unavailable ({exc}); showing the board without ADP.[/yellow]")

    consensus = consensus_rows(
        store,
        season=season,
        position=None,  # board is full; --pos filters only the terminal view
        sources=active_sources,
        cfg=league.scoring,
    )
    # On failure, ignore any ADP persisted by a prior run — otherwise the board
    # (and export) would serve stale ADP/ranks despite the "unavailable" notice.
    adp = [] if adp_failed else store.adp_rows(season)
    store.close()

    board = board_mod.board_rows(
        consensus,
        adp,
        roster_slots=league.roster_slots,
        num_teams=league.num_teams,
    )

    if not board:
        console.print(f"[yellow]No projections or ADP for {season}.[/yellow]")
        raise typer.Exit(code=0)

    if export:
        out_dir = Path(export_dir) if export_dir else paths.export_dir()
        _export_board(board, season=season, out_dir=out_dir, league=league)

    shown = [r for r in board if pos is None or (r["pos"] or "").upper() == pos.upper()]
    shown = shown[:limit]
    _render_board(shown, season=season, pos=pos)
    _report_board_unmatched(shown)
    _report_adp_unmatched(adp_recon)
    _report_scoring_provenance(league)


def _export_board(board: list[dict], *, season: int, out_dir: Path, league: object) -> None:
    """Write cheatsheet.md, cheatsheet.csv, and board.json into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    doc = board_mod.to_board_json(
        board,
        season=season,
        num_teams=league.num_teams,
        roster_slots=league.roster_slots,
        generated_at=generated_at,
        scoring=league.scoring_provenance,
    )
    (out_dir / "cheatsheet.md").write_text(board_mod.to_markdown(board, season=season))
    (out_dir / "cheatsheet.csv").write_text(board_mod.to_csv(board))
    (out_dir / "board.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    console.print(
        f"[green]Wrote[/green] {out_dir / 'cheatsheet.md'}, "
        f"{out_dir / 'cheatsheet.csv'}, {out_dir / 'board.json'}"
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
    """Surface crosswalk misses among the shown board players."""
    misses = [r for r in rows if not r["matched"]]
    if not misses:
        return
    names = ", ".join(r["name"] for r in misses[:8])
    more = "…" if len(misses) > 8 else ""
    console.print(
        f"[yellow]⚠ {len(misses)} shown player(s) unmatched to crosswalk "
        f"(source-only): {names}{more}[/yellow]"
    )


def _report_adp_unmatched(recon: Reconciliation) -> None:
    """Surface ADP rows that didn't resolve to the crosswalk (DEF, name misses)."""
    if not recon.unmatched:
        return
    names = ", ".join(recon.unmatched_names[:8])
    more = "…" if recon.unmatched > 8 else ""
    console.print(
        f"[yellow]⚠ {recon.unmatched} ADP player(s) unmatched to crosswalk: {names}{more}[/yellow]"
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
    """Surface crosswalk misses among the shown players (never silently drop)."""
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

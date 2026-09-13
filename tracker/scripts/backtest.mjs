// Offline draft-recommendation backtest runner.
//   npm run backtest -- [--board ../exports/board.json] [--draft ../drafts/mcffl-2026.json] [--delta 0.15[,0.25]] [--all-teams] [--json]
// Bundled by esbuild so it can import the tracker's TypeScript sources.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { asUserTeam, backtestDraft, boardRankRanker, liveLineupRanker, marketRanker } from "../src/backtest.ts";
import { parseReplayDraft } from "../src/draft-replay.ts";

const args = process.argv.slice(2);
const option = (name, fallback) => {
  const index = args.indexOf(`--${name}`);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
};
const boardPath = resolve(option("board", "../exports/board.json"));
const draftPath = resolve(option("draft", "../drafts/mcffl-2026.json"));
const board = JSON.parse(readFileSync(boardPath, "utf8"));
const saved = parseReplayDraft(JSON.parse(readFileSync(draftPath, "utf8")));
// --delta sweeps the depth weight; each value runs as its own live ranker.
const deltas = option("delta", null)?.split(",").map(Number);
const rankers = [boardRankRanker, marketRanker, ...(deltas
  ? deltas.map(depthWeight => liveLineupRanker({ depthWeight }, `live@${depthWeight}`))
  : [liveLineupRanker()])];
const fmt = (n) => (n === null || n === undefined ? "—" : n.toFixed(1));

// --all-teams replays the saved draft from every team's seat: a summary of
// followed totals by draft slot, without per-turn detail.
if (args.includes("--all-teams")) {
  const reports = [...saved.teams].sort((a, b) => a.draft_slot - b.draft_slot)
    .map(team => ({ team, report: backtestDraft(asUserTeam(saved, team.id), board, rankers) }));
  if (args.includes("--json")) {
    console.log(JSON.stringify(reports.map(({ team, report }) => ({ draft_slot: team.draft_slot + 1, ...report })), null, 2));
    process.exit(0);
  }
  const names = rankers.map(r => r.name);
  console.log(`${saved.draft.name} · every seat · board ${board.season} generated ${board.generated_at}\n`);
  console.log(`slot  ${"actual".padStart(8)}${names.map(n => n.padStart(10)).join("")}`);
  const totals = names.map(() => 0);
  let actual = 0;
  for (const { team, report } of reports) {
    actual += report.actualTotal;
    report.rankers.forEach((r, i) => { totals[i] += r.followedTotal; });
    console.log(`${String(team.draft_slot + 1).padStart(4)}  ${fmt(report.actualTotal).padStart(8)}${report.rankers.map(r => fmt(r.followedTotal).padStart(10)).join("")}`);
  }
  console.log(`${"all".padStart(4)}  ${fmt(actual).padStart(8)}${totals.map(t => fmt(t).padStart(10)).join("")}`);
  process.exit(0);
}

const report = backtestDraft(saved, board, rankers);
if (args.includes("--json")) {
  console.log(JSON.stringify(report, null, 2));
  process.exit(0);
}
const label = (p) => (p ? `${p.name} (${p.pos} #${p.rank})` : "—");
console.log(`${report.draft} · ${report.user} · board ${board.season} generated ${board.generated_at}`);
console.log(`Actual projected starting lineup: ${fmt(report.actualTotal)}\n`);
console.log("ranker        followed   vs actual   agreement");
for (const r of report.rankers) {
  console.log(`${r.ranker.padEnd(12)} ${fmt(r.followedTotal).padStart(9)} ${fmt(r.followedTotal - report.actualTotal).padStart(11)} ${(r.agreement * 100).toFixed(0).padStart(10)}%`);
}
for (const r of report.rankers) {
  console.log(`\n== ${r.ranker}: recommendation at each own turn (gain = projected lineup gain given roster held then)`);
  for (const p of r.picks) {
    console.log(`R${String(p.round).padStart(2)} #${String(p.overall_pick).padStart(3)}  ${p.agreed ? "=" : " "} actual ${label(p.actual).padEnd(34)} +${fmt(p.actualGain).padStart(5)}   rec ${label(p.recommended).padEnd(34)} +${fmt(p.recommendedGain).padStart(5)}${p.recommendedReason ? `   ${p.recommendedReason}` : ""}`);
  }
  console.log(`   followed roster: ${r.followedRoster.map(p => `${p.name} ${p.pos}`).join(", ")}`);
}

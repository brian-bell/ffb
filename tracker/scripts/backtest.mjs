// Offline draft-recommendation backtest runner.
//   npm run backtest -- [--board ../exports/board.json] [--draft ../drafts/mcffl-2026.json] [--json]
// Bundled by esbuild so it can import the tracker's TypeScript sources.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { backtestDraft, builtinRankers } from "../src/backtest.ts";
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
const report = backtestDraft(saved, board, builtinRankers);

if (args.includes("--json")) {
  console.log(JSON.stringify(report, null, 2));
  process.exit(0);
}
const fmt = (n) => (n === null || n === undefined ? "—" : n.toFixed(1));
const label = (p) => (p ? `${p.name} (${p.pos} #${p.rank})` : "—");
console.log(`${report.draft} · ${report.user} · board ${board.season} generated ${board.generated_at}`);
console.log(`Actual projected starting lineup: ${fmt(report.actualTotal)}\n`);
console.log("ranker      followed   vs actual   agreement");
for (const r of report.rankers) {
  console.log(`${r.ranker.padEnd(10)} ${fmt(r.followedTotal).padStart(9)} ${fmt(r.followedTotal - report.actualTotal).padStart(11)} ${(r.agreement * 100).toFixed(0).padStart(10)}%`);
}
for (const r of report.rankers) {
  console.log(`\n== ${r.ranker}: recommendation at each own turn (gain = projected lineup gain given roster held then)`);
  for (const p of r.picks) {
    console.log(`R${String(p.round).padStart(2)} #${String(p.overall_pick).padStart(3)}  ${p.agreed ? "=" : " "} actual ${label(p.actual).padEnd(34)} +${fmt(p.actualGain).padStart(5)}   rec ${label(p.recommended).padEnd(34)} +${fmt(p.recommendedGain)}`);
  }
  console.log(`   followed roster: ${r.followedRoster.map(p => `${p.name} ${p.pos}`).join(", ")}`);
}

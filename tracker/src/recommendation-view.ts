import type { RecommendationState } from "./recommendation";

function esc(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** Small pure view-model formatter; browser wiring only inserts this trusted output. */
export function recommendationHtml(state: RecommendationState): string {
  const recommendation = state.recommendation;
  if (!recommendation) return "";
  const tier = recommendation.tier === null ? "UNTIERED" : `TIER ${recommendation.tier}${recommendation.tierRemaining === null ? "" : ` · ${recommendation.tierRemaining} LEFT`}`;
  const vorp = typeof recommendation.player.vorp === "number" ? recommendation.player.vorp.toFixed(1) : "—";
  return `<button class="recommendation" data-recommendation-key="${encodeURIComponent(recommendation.player.key)}"><span class="eyebrow">YOUR PICK</span><b>${esc(recommendation.position)} · ${esc(recommendation.player.name)}</b><small>${tier} · ${vorp} VORP</small><span>${esc(recommendation.reason)}</span><em>Select recommendation</em></button>`;
}

/** Compact recommendation facts for the always-visible pick dock summary. */
export function recommendationSummaryHtml(state: RecommendationState): string {
  const recommendation = state.recommendation;
  if (!recommendation) {
    return '<span class="dock-label">DRAFT ASSISTANT</span><span class="dock-pick">No recommendation for this turn.</span>';
  }
  const tier = recommendation.tier === null
    ? "UNTIERED"
    : `TIER ${recommendation.tier}${recommendation.tierRemaining === null ? "" : ` · ${recommendation.tierRemaining} LEFT`}`;
  return (
    '<span class="dock-label">YOUR PICK · RECOMMENDATION</span>' +
    `<span class="dock-pick"><b>${esc(recommendation.position)} · ${esc(recommendation.player.name)}</b>` +
    `<small>${esc(tier)}</small></span>`
  );
}

export function needsHtml(state: RecommendationState): string {
  if (!state.context) return "";
  const { roster } = state.context;
  const needs = [
    ...Object.entries(roster.openDedicated).filter(([, count]) => count > 0).map(([pos, count]) => `${pos}${count > 1 ? ` ×${count}` : ""}`),
    ...(roster.openFlex ? [`FLEX${roster.openFlex > 1 ? ` ×${roster.openFlex}` : ""}`] : []),
  ];
  return needs.length ? `NEEDS&nbsp;&nbsp;${needs.map(esc).join("&nbsp;&nbsp;")}` : "Starters filled";
}

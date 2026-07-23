export interface SelectablePlayerRow {
  classList: Pick<DOMTokenList, "toggle">;
  setAttribute(name: string, value: string): void;
}

export interface PlayerSelectionList {
  querySelector(selector: string): SelectablePlayerRow | null;
}

export interface RecordedPlayerElement {
  getAttribute(name: string): string | null;
  setAttribute(name: string, value: string): void;
  textContent: string | null;
  remove(): void;
}

export interface RecordedPlayerList {
  querySelector(selector: string): RecordedPlayerElement | null;
}

export interface BusyPlayerList {
  toggleAttribute(name: string, force?: boolean): void;
  setAttribute(name: string, value: string): void;
}

export interface TieredPlayerIdentity extends PlayerIdentity {
  tier: number | null;
}

function setSelected(row: SelectablePlayerRow, selected: boolean): void {
  row.classList.toggle("selected", selected);
  row.setAttribute("aria-pressed", String(selected));
}

/**
 * Reflect the selected player in the existing board rows. Selection changes
 * never need to rebuild the full board: at most the prior and next row change.
 */
export function syncSelectedPlayerRow(list: PlayerSelectionList, selectedKey: string | null): void {
  const prior = list.querySelector(".rowA.selectable.selected");
  if (prior) setSelected(prior, false);

  if (selectedKey === null) return;
  const next = list.querySelector(`button[data-player-key="${encodeURIComponent(selectedKey)}"]`);
  if (next) setSelected(next, true);
}

/**
 * Remove a successfully recorded player from the current available view
 * without rebuilding thousands of unaffected rows.
 */
export function removeRecordedPlayerRows(
  list: RecordedPlayerList,
  players: readonly TieredPlayerIdentity[],
  recordedPlayer: TieredPlayerIdentity,
): boolean {
  const removedByTier = new Map<string, number>();
  let recordedRowRemoved = false;
  for (const player of players) {
    if (!playersEquivalent(player, recordedPlayer)) continue;
    const row = list.querySelector(`button[data-player-key="${encodeURIComponent(player.key)}"]`);
    if (!row) continue;

    row.remove();
    if (player.key === recordedPlayer.key) recordedRowRemoved = true;
    const tierKey = player.tier == null ? "adp" : String(player.tier);
    removedByTier.set(tierKey, (removedByTier.get(tierKey) ?? 0) + 1);
  }
  if (!recordedRowRemoved) return false;

  for (const [tierKey, removedCount] of removedByTier) {
    const divider = list.querySelector(`.trule[data-tier-key="${tierKey}"]`);
    if (!divider) continue;
    const count = Number(divider.getAttribute("data-tier-count"));
    if (!Number.isInteger(count) || count <= 0) return false;
    if (count <= removedCount) {
      divider.remove();
      continue;
    }

    const nextCount = count - removedCount;
    divider.setAttribute("data-tier-count", String(nextCount));
    divider.textContent = divider.textContent?.replace(/\d+ left$/, `${nextCount} left`) ?? null;
  }

  return true;
}

/** Block interactions with the existing rows while a draft write is pending. */
export function setPlayerListBusy(list: BusyPlayerList, busy: boolean): void {
  list.toggleAttribute("inert", busy);
  list.setAttribute("aria-busy", String(busy));
}
import { playersEquivalent, type PlayerIdentity } from "./player-identity";

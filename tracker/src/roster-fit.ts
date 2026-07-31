const FLEX_POSITIONS: Readonly<Record<string, readonly string[]>> = {
  "W/T": ["WR", "TE"],
  "W/R/T": ["RB", "WR", "TE"],
};

const DEDICATED_POSITIONS = new Set(["QB", "RB", "WR", "TE", "K", "DEF"]);

import type { MockPick } from "./mock-draft";
import { distinctPlayerIdentities } from "./player-identity";
import type { Player } from "./types";

export interface RosterFit {
  legal: boolean;
  filledStarters: number;
  openStarters: number;
}

function positiveSlots(rosterSlots: Readonly<Record<string, number>>): string[] | null {
  const slots: string[] = [];
  for (const [label, count] of Object.entries(rosterSlots)) {
    if (!Number.isInteger(count) || count < 0) return null;
    if (count === 0) continue;
    if (label !== "BN" && !DEDICATED_POSITIONS.has(label) && !(label in FLEX_POSITIONS)) {
      return null;
    }
    slots.push(...Array.from({ length: count }, () => label));
  }
  return slots;
}

function accepts(slot: string, position: string): boolean {
  if (slot === "BN") return DEDICATED_POSITIONS.has(position);
  if (DEDICATED_POSITIONS.has(slot)) return slot === position;
  return FLEX_POSITIONS[slot]?.includes(position) === true;
}

function maximumMatching(slots: readonly string[], positions: readonly string[]): number {
  const playerForSlot = Array<number>(slots.length).fill(-1);

  function assign(player: number, visited: boolean[]): boolean {
    for (let slot = 0; slot < slots.length; slot += 1) {
      if (visited[slot] || !accepts(slots[slot]!, positions[player]!)) continue;
      visited[slot] = true;
      if (playerForSlot[slot] === -1 || assign(playerForSlot[slot]!, visited)) {
        playerForSlot[slot] = player;
        return true;
      }
    }
    return false;
  }

  let matched = 0;
  for (let player = 0; player < positions.length; player += 1) {
    if (assign(player, Array(slots.length).fill(false))) matched += 1;
  }
  return matched;
}

export function rosterFit(
  rosterSlots: Readonly<Record<string, number>>,
  positions: readonly string[],
): RosterFit {
  const slots = positiveSlots(rosterSlots);
  const starterSlots = slots?.filter((slot) => slot !== "BN") ?? [];
  const filledStarters = maximumMatching(starterSlots, positions);
  const legal = slots !== null
    && positions.every((position) => DEDICATED_POSITIONS.has(position))
    && maximumMatching(slots, positions) === positions.length;
  return {
    legal,
    filledStarters,
    openStarters: starterSlots.length - filledStarters,
  };
}

export function isCompleteRoster(
  rosterSlots: Readonly<Record<string, number>>,
  positions: readonly string[],
): boolean {
  const slots = positiveSlots(rosterSlots);
  return slots !== null && positions.length === slots.length && rosterFit(rosterSlots, positions).legal;
}

interface FlowEdge {
  to: number;
  reverse: number;
  capacity: number;
}

function addEdge(graph: FlowEdge[][], from: number, to: number, capacity: number): void {
  const forward = { to, reverse: graph[to]!.length, capacity };
  const reverse = { to: from, reverse: graph[from]!.length, capacity: 0 };
  graph[from]!.push(forward);
  graph[to]!.push(reverse);
}

function maxFlow(graph: FlowEdge[][], source: number, sink: number): number {
  let total = 0;
  while (true) {
    const levels = Array(graph.length).fill(-1) as number[];
    levels[source] = 0;
    const queue = [source];
    for (let cursor = 0; cursor < queue.length; cursor += 1) {
      const node = queue[cursor]!;
      for (const edge of graph[node]!) {
        if (edge.capacity > 0 && levels[edge.to] === -1) {
          levels[edge.to] = levels[node]! + 1;
          queue.push(edge.to);
        }
      }
    }
    if (levels[sink] === -1) return total;
    const cursors = Array(graph.length).fill(0) as number[];
    const send = (node: number, amount: number): number => {
      if (node === sink) return amount;
      while (cursors[node]! < graph[node]!.length) {
        const edge = graph[node]![cursors[node]!]!;
        if (edge.capacity > 0 && levels[edge.to] === levels[node]! + 1) {
          const sent = send(edge.to, Math.min(amount, edge.capacity));
          if (sent > 0) {
            edge.capacity -= sent;
            graph[edge.to]![edge.reverse]!.capacity += sent;
            return sent;
          }
        }
        cursors[node] = cursors[node]! + 1;
      }
      return 0;
    };
    while (true) {
      const sent = send(source, Number.MAX_SAFE_INTEGER);
      if (sent === 0) break;
      total += sent;
    }
  }
}

interface RosterPickLike {
  draft_slot: number;
  player_pos: string | null;
}

function leagueCanComplete(
  rosterSlots: Readonly<Record<string, number>>,
  teamCount: number,
  picks: readonly RosterPickLike[],
  availablePositions: readonly string[],
): boolean {
  const slotLabels = positiveSlots(rosterSlots);
  if (!slotLabels || !Number.isInteger(teamCount) || teamCount < 1) return false;
  if (picks.some((pick) =>
    !Number.isInteger(pick.draft_slot)
    || pick.draft_slot < 0
    || pick.draft_slot >= teamCount
    || typeof pick.player_pos !== "string"
    || !DEDICATED_POSITIONS.has(pick.player_pos)
  )) return false;

  const pickedGroups = new Map<string, { position: string; draftSlot: number; count: number }>();
  for (const pick of picks) {
    const key = `${pick.draft_slot}:${pick.player_pos}`;
    const group = pickedGroups.get(key);
    if (group) group.count += 1;
    else pickedGroups.set(key, {
      position: pick.player_pos as string,
      draftSlot: pick.draft_slot,
      count: 1,
    });
  }
  const availableGroups = new Map<string, number>();
  for (const position of availablePositions) {
    availableGroups.set(position, (availableGroups.get(position) ?? 0) + 1);
  }
  const groups = [
    ...[...pickedGroups.values()].map((group) => ({ ...group, picked: true })),
    ...[...availableGroups].map(([position, count]) => ({
      position,
      draftSlot: -1,
      count,
      picked: false,
    })),
  ];
  const playerCount = picks.length + availablePositions.length;
  const leagueSlotCount = slotLabels.length * teamCount;
  if (playerCount < leagueSlotCount || picks.length > leagueSlotCount) return false;

  const source = 0;
  const firstPlayer = 1;
  const firstSlot = firstPlayer + groups.length;
  const sink = firstSlot + leagueSlotCount;
  const superSource = sink + 1;
  const superSink = sink + 2;
  const graph = Array.from({ length: superSink + 1 }, () => [] as FlowEdge[]);
  const demand = Array(graph.length).fill(0) as number[];

  const boundedEdge = (from: number, to: number, lower: number, upper: number) => {
    addEdge(graph, from, to, upper - lower);
    demand[from] = demand[from]! - lower;
    demand[to] = demand[to]! + lower;
  };

  for (let groupIndex = 0; groupIndex < groups.length; groupIndex += 1) {
    const group = groups[groupIndex]!;
    const playerNode = firstPlayer + groupIndex;
    boundedEdge(source, playerNode, group.picked ? group.count : 0, group.count);
    for (let team = 0; team < teamCount; team += 1) {
      if (group.picked && group.draftSlot !== team) continue;
      for (let slotIndex = 0; slotIndex < slotLabels.length; slotIndex += 1) {
        if (!accepts(slotLabels[slotIndex]!, group.position)) continue;
        const slotNode = firstSlot + team * slotLabels.length + slotIndex;
        boundedEdge(playerNode, slotNode, 0, 1);
      }
    }
  }
  for (let slot = 0; slot < leagueSlotCount; slot += 1) {
    boundedEdge(firstSlot + slot, sink, 1, 1);
  }
  addEdge(graph, sink, source, leagueSlotCount);

  let required = 0;
  for (let node = 0; node <= sink; node += 1) {
    if (demand[node]! > 0) {
      addEdge(graph, superSource, node, demand[node]!);
      required += demand[node]!;
    } else if (demand[node]! < 0) {
      addEdge(graph, node, superSink, -demand[node]!);
    }
  }
  return maxFlow(graph, superSource, superSink) === required;
}

export interface SafePositionsContext {
  rosterSlots: Readonly<Record<string, number>>;
  teamCount: number;
  picks: readonly MockPick[];
  available: readonly Player[];
  draftSlot: number;
}

export function safePositionsForTurn(context: SafePositionsContext): ReadonlySet<string> {
  const distinctAvailable = distinctPlayerIdentities(context.available);
  const positions = [...new Set(
    distinctAvailable
      .map((player) => player.pos)
      .filter((position): position is string =>
        typeof position === "string" && DEDICATED_POSITIONS.has(position)
      ),
  )];
  const safe = new Set<string>();
  for (const position of positions) {
    const remaining = distinctAvailable.map((player) => player.pos);
    const removed = remaining.indexOf(position);
    if (removed === -1) continue;
    remaining.splice(removed, 1);
    const candidatePick: RosterPickLike = {
      draft_slot: context.draftSlot,
      player_pos: position,
    };
    if (leagueCanComplete(
      context.rosterSlots,
      context.teamCount,
      [...context.picks, candidatePick],
      remaining.filter((value): value is string => typeof value === "string"),
    )) safe.add(position);
  }
  return safe;
}

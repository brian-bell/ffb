import { nextPick, type DraftTeam, type NextPick } from "./draft";
import {
  normalizeInitialRngState,
  type OpponentStrategy,
  type VariancePreset,
} from "./mock-strategy";
import { isAvailable } from "./player-identity";
import { buildPlayerPool, type PlayerPoolPick } from "./player-pool";
import { safePositionsForTurn } from "./roster-fit";
import type { Board } from "./types";

export interface MockSetup {
  user_slot: number;
  seed: number;
  variance_preset?: VariancePreset;
}

export interface MockInfo {
  id: string;
  board_fingerprint: string;
  seed: number;
  strategy_version: string;
  user_slot: number;
  team_count: number;
  rounds: number;
  variance_preset: VariancePreset;
}

export interface MockPick extends PlayerPoolPick {
  draft_slot: number;
  source: "user" | "simulated";
}

export interface MockAggregate {
  seed: number;
  strategy_version: string;
  user_slot: number;
  team_count: number;
  rounds: number;
  variance_preset: VariancePreset;
  teams: DraftTeam[];
  picks: MockPick[];
  next: NextPick | null;
  complete: boolean;
  revision: number;
  rng_state: number;
}

export interface MockState {
  configured: boolean;
  board?: Board;
  board_error?: string;
  mock?: MockInfo;
  teams?: DraftTeam[];
  picks: MockPick[];
  next?: NextPick | null;
  complete?: boolean;
  revision: number;
  appended_picks?: MockPick[];
}

export interface MockTransition {
  appended_picks: MockPick[];
  next_rng_state: number;
  next: NextPick | null;
  complete: boolean;
}

export type MockDomainErrorCode =
  | "invalid_mock"
  | "board_unusable"
  | "mock_complete"
  | "not_user_turn"
  | "unknown_player"
  | "player_unavailable"
  | "illegal_roster_pick";

export class MockDomainError extends Error {
  constructor(readonly code: MockDomainErrorCode, message: string) {
    super(message);
    this.name = "MockDomainError";
  }
}

function mockRounds(board: Board): number {
  return Object.values(board.roster_slots).reduce(
    (total, count) => total + (Number.isInteger(count) && count > 0 ? count : 0),
    0,
  );
}

function mockTeams(teamCount: number, userSlot: number): DraftTeam[] {
  return Array.from({ length: teamCount }, (_, draftSlot) => {
    const isUser = draftSlot === userSlot - 1;
    return {
      id: draftSlot + 1,
      name: isUser ? "Brian" : `CPU ${draftSlot + 1}`,
      draft_slot: draftSlot,
      is_user: isUser,
    };
  });
}

function validUnsignedSeed(seed: number): boolean {
  return Number.isInteger(seed) && seed >= 0 && seed <= 0xffff_ffff;
}

function distinctPlayerCount(board: Board): number {
  const distinct = [];
  for (const player of board.players) {
    if (isAvailable(player, distinct)) distinct.push(player);
  }
  return distinct.length;
}

interface AdvanceResult {
  picks: MockPick[];
  next: NextPick | null;
  rng_state: number;
}

function autoAdvance(
  board: Board,
  teams: DraftTeam[],
  rounds: number,
  existingPicks: readonly MockPick[],
  rngState: number,
  strategy: OpponentStrategy,
  variancePreset: VariancePreset,
): AdvanceResult {
  const picks = [...existingPicks];
  let next = nextPick(teams, rounds, picks.length + 1);
  let nextRngState = rngState;

  while (next && !next.is_user) {
    const available = buildPlayerPool(board.players, picks).available;
    const team = teams.find((candidate) => candidate.id === next!.team_id);
    if (!team) {
      throw new MockDomainError("board_unusable", "The mock team configuration is invalid.");
    }
    const safePositions = safePositionsForTurn({
      rosterSlots: board.roster_slots,
      teamCount: teams.length,
      picks,
      available,
      draftSlot: team.draft_slot,
    });
    const candidates = available.filter((player) =>
      typeof player.pos === "string" && safePositions.has(player.pos)
    );
    if (candidates.length === 0) {
      throw new MockDomainError("board_unusable", "No legal player remains for the simulated turn.");
    }
    let choice;
    try {
      choice = strategy.choose({
        candidates,
        team_picks: picks.filter((pick) => pick.draft_slot === team.draft_slot),
        roster_slots: board.roster_slots,
        next,
        rounds,
        variance_preset: variancePreset,
        rng_state: nextRngState,
      });
    } catch {
      throw new MockDomainError("board_unusable", "The opponent strategy could not choose a legal player.");
    }
    const player = candidates.find((candidate) => candidate.key === choice.player_key);
    if (!player || !team || !validUnsignedSeed(choice.next_rng_state)) {
      throw new MockDomainError("board_unusable", "The opponent strategy returned an invalid choice.");
    }
    picks.push({
      overall_pick: next.overall_pick,
      round: next.round,
      round_pick: next.round_pick,
      team_name: next.team_name,
      draft_slot: team.draft_slot,
      player_key: player.key,
      player_name: player.name,
      player_pos: player.pos,
      player_team: player.team,
      source: "simulated",
    });
    nextRngState = choice.next_rng_state;
    next = nextPick(teams, rounds, picks.length + 1);
  }

  return { picks, next, rng_state: nextRngState };
}

export function startMock(board: Board, setup: MockSetup, strategy: OpponentStrategy): MockAggregate {
  const teamCount = board.num_teams;
  const rounds = mockRounds(board);
  const playerCount = distinctPlayerCount(board);
  if (
    !Number.isInteger(teamCount)
    || teamCount < 2
    || rounds < 1
    || playerCount < teamCount * rounds
  ) {
    throw new MockDomainError("board_unusable", "The board cannot support a mock draft.");
  }
  if (
    !Number.isInteger(setup.user_slot)
    || setup.user_slot < 1
    || setup.user_slot > teamCount
    || !validUnsignedSeed(setup.seed)
  ) {
    throw new MockDomainError("invalid_mock", "Choose a valid draft slot and unsigned 32-bit seed.");
  }

  const teams = mockTeams(teamCount, setup.user_slot);
  const variancePreset = setup.variance_preset ?? "realistic";
  const initialRngState = strategy.version === "market-need-v1"
    ? normalizeInitialRngState(setup.seed)
    : setup.seed;
  const initialAvailable = buildPlayerPool(board.players, []).available;
  if (safePositionsForTurn({
    rosterSlots: board.roster_slots,
    teamCount,
    picks: [],
    available: initialAvailable,
    draftSlot: setup.user_slot - 1,
  }).size === 0) {
    throw new MockDomainError("board_unusable", "The board's player pool cannot complete every roster.");
  }
  const advanced = autoAdvance(
    board,
    teams,
    rounds,
    [],
    initialRngState,
    strategy,
    variancePreset,
  );

  return {
    seed: setup.seed,
    strategy_version: strategy.version,
    user_slot: setup.user_slot,
    team_count: teamCount,
    rounds,
    variance_preset: variancePreset,
    teams,
    picks: advanced.picks,
    next: advanced.next,
    complete: advanced.next === null,
    revision: advanced.picks.length,
    rng_state: advanced.rng_state,
  };
}

export function recordUserPick(
  aggregate: MockAggregate,
  board: Board,
  playerKey: string,
  strategy: OpponentStrategy,
): MockTransition {
  if (aggregate.complete || aggregate.next === null) {
    throw new MockDomainError("mock_complete", "The mock draft is complete.");
  }
  if (!aggregate.next.is_user) {
    throw new MockDomainError("not_user_turn", "Brian is not on the clock.");
  }
  const boardPlayer = board.players.find((player) => player.key === playerKey);
  if (!boardPlayer) {
    throw new MockDomainError("unknown_player", "The player is not on this mock's board.");
  }
  const availablePlayer = buildPlayerPool(board.players, aggregate.picks).available.find(
    (player) => player.key === boardPlayer.key,
  );
  if (!availablePlayer) {
    throw new MockDomainError("player_unavailable", "The player has already been drafted.");
  }
  const team = aggregate.teams.find((candidate) => candidate.id === aggregate.next!.team_id);
  if (!team) {
    throw new MockDomainError("board_unusable", "The mock team configuration is invalid.");
  }
  const safePositions = safePositionsForTurn({
    rosterSlots: board.roster_slots,
    teamCount: aggregate.team_count,
    picks: aggregate.picks,
    available: buildPlayerPool(board.players, aggregate.picks).available,
    draftSlot: team.draft_slot,
  });
  if (!availablePlayer.pos || !safePositions.has(availablePlayer.pos)) {
    throw new MockDomainError(
      "illegal_roster_pick",
      "That pick would make at least one configured roster impossible to complete.",
    );
  }

  const userPick: MockPick = {
    overall_pick: aggregate.next.overall_pick,
    round: aggregate.next.round,
    round_pick: aggregate.next.round_pick,
    team_name: aggregate.next.team_name,
    draft_slot: team.draft_slot,
    player_key: availablePlayer.key,
    player_name: availablePlayer.name,
    player_pos: availablePlayer.pos,
    player_team: availablePlayer.team,
    source: "user",
  };
  const advanced = autoAdvance(
    board,
    aggregate.teams,
    aggregate.rounds,
    [...aggregate.picks, userPick],
    aggregate.rng_state,
    strategy,
    aggregate.variance_preset,
  );

  return {
    appended_picks: advanced.picks.slice(aggregate.picks.length),
    next_rng_state: advanced.rng_state,
    next: advanced.next,
    complete: advanced.next === null,
  };
}

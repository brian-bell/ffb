// The slice-5 board.json v1 contract, consumed read-only. Field names mirror
// ffb.board._BOARD_FIELDS and the envelope in ffb.board.to_board_json.

export interface Player {
  key: string;
  name: string;
  pos: string | null;
  team: string | null;
  bye: number | null;
  points: number | null;
  n_sources: number;
  vorp: number | null;
  tier: number | null;
  rank: number;
  pos_rank: number;
  adp: number | null;
  adp_rank: number | null;
  adp_high: number | null;
  adp_low: number | null;
  adp_stdev: number | null;
  matched: boolean;
}

export interface Board {
  version: number;
  season: number;
  generated_at: string;
  scoring: string;
  num_teams: number;
  roster_slots: Record<string, number>;
  players: Player[];
}

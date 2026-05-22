/**
 * Cloudflare Workers Bindings and shared response types for keiba-api.
 *
 * Bindings.KEIBA_DB matches `binding = "KEIBA_DB"` in wrangler.toml.
 */

export interface Bindings {
  KEIBA_DB: D1Database;
}

// ---------------------------------------------------------------------------
// Shared scalar helpers
// ---------------------------------------------------------------------------

export type Nullable<T> = T | null;

/** Comma-separated multi-value filter string (e.g. "東京,中山"). */
export type MultiFilter = string | undefined;

// ---------------------------------------------------------------------------
// Response stubs (filled in as endpoints are migrated)
// ---------------------------------------------------------------------------

export interface MetaResponse {
  venues: string[];
  classes: string[];
  surfaces: string[];
  conditions: string[];
  weathers: string[];
  genders: string[];
  years: number[];
  distances: number[];
  race_names_graded: string[];
  total_rows: number;
}

export interface SummaryResponse {
  total_races: number;
  total_entries: number;
  avg_win_odds: Nullable<number>;
  fav_win_rate: Nullable<number>;
  fav_place_rate: Nullable<number>;
  avg_win_payout: Nullable<number>;
  avg_trifecta: Nullable<number>;
  avg_runners: Nullable<number>;
  avg_last_3f_winner: Nullable<number>;
}

export interface HeatmapCell {
  row: string;
  col: string;
  n: number;
  win_rate: number;
  place_rate: number;
  avg_finish: Nullable<number>;
  roi: number;
}

export interface HeatmapTotal {
  n: number;
  win_rate: number;
  place_rate: number;
  avg_finish: Nullable<number>;
  roi: number;
}

export interface HeatmapResponse {
  cells: HeatmapCell[];
  row_totals: Record<string, HeatmapTotal>;
  col_totals: Record<string, HeatmapTotal>;
  grand_total: HeatmapTotal;
}

export interface RotationPrevRace {
  prev_race: string;
  n: number;
  win_rate: Nullable<number>;
  top2_rate: Nullable<number>;
  top3_rate: Nullable<number>;
  avg_finish: Nullable<number>;
  avg_pop: Nullable<number>;
  roi: Nullable<number>;
}

export interface RotationDetail {
  prev_race: string;
  prev_finish: Nullable<number>;
  n: number;
  win_rate: Nullable<number>;
  top3_rate: Nullable<number>;
  avg_finish: Nullable<number>;
}

export interface RotationSummary {
  race_count: number;
  fav_win_rate: Nullable<number>;
  avg_win_payout: Nullable<number>;
  top_prev_race: Nullable<string>;
}

export interface RotationResponse {
  summary: RotationSummary;
  prev_races: RotationPrevRace[];
  prev_finish_detail: RotationDetail[];
}

export interface StrengthScanRow {
  condition_label: string;
  n: number;
  win_rate: Nullable<number>;
  place_rate: Nullable<number>;
  avg_finish: Nullable<number>;
  roi: Nullable<number>;
  avg_payout: Nullable<number>;
  confidence: Nullable<number>;
}

export interface UpsetRow {
  label: string;
  group_type: "venue" | "surface_distance" | "class";
  race_count: number;
  fav_win_rate: Nullable<number>;
  avg_win_payout: Nullable<number>;
  upset_score: Nullable<number>;
}

export interface UpsetResponse {
  upset_prone: UpsetRow[];
  predictable: UpsetRow[];
}

export interface SearchStats {
  n: number;
  win_rate: Nullable<number>;
  top3_rate: Nullable<number>;
  avg_finish: Nullable<number>;
  avg_pop: Nullable<number>;
  avg_odds: Nullable<number>;
  roi: Nullable<number>;
  wins: number;
  top2: number;
  top3: number;
}

export interface SearchResponse {
  stats: Nullable<SearchStats>;
  records: Record<string, unknown>[];
}

export interface EntriesResponse {
  total: number;
  page: number;
  per_page: number;
  pages: number;
  data: Record<string, unknown>[];
}

export interface ConditionStrengthBreakdown {
  class: string;
  n: number;
  win_rate: Nullable<number>;
  place_rate: Nullable<number>;
}

export interface ConditionStrengthResponse {
  n: number;
  win_rate: Nullable<number>;
  place_rate: Nullable<number>;
  top2_rate: Nullable<number>;
  avg_finish: Nullable<number>;
  avg_pop: Nullable<number>;
  by_class: ConditionStrengthBreakdown[];
}

export interface HorseFormBreakdown {
  surface_distance: string;
  n: number;
  win_rate: Nullable<number>;
  place_rate: Nullable<number>;
  avg_finish: Nullable<number>;
}

export interface HorseFormResponse {
  total_runs: number;
  win_rate: Nullable<number>;
  place_rate: Nullable<number>;
  avg_finish: Nullable<number>;
  avg_pop: Nullable<number>;
  recent: Record<string, unknown>[];
  condition_breakdown: HorseFormBreakdown[];
}

export interface RaceEntriesHorse {
  name: string;
  jockey: Nullable<string>;
  sire: Nullable<string>;
  prev_race: Nullable<string>;
  prev_finish: Nullable<number>;
  number: Nullable<number>;
  popularity: Nullable<number>;
  finish: Nullable<number>;
  win_odds: Nullable<number>;
}

export interface RaceEntriesResponse {
  race_name: string;
  year: number;
  venue: Nullable<string>;
  surface: Nullable<string>;
  distance: Nullable<number>;
  condition: Nullable<string>;
  available_years: number[];
  horses: RaceEntriesHorse[];
}

// POST /api/race-analysis -----------------------------------------------------

export interface RaceAnalysisHorseInput {
  name: string;
  jockey?: Nullable<string>;
  sire?: Nullable<string>;
  prev_race?: Nullable<string>;
  prev_finish?: Nullable<number>;
}

export interface RaceAnalysisRequest {
  race_name?: Nullable<string>;
  venue: string;
  surface: string;
  distance: number;
  condition?: Nullable<string>;
  horses: RaceAnalysisHorseInput[];
}

export interface RaceAnalysisFactorScore {
  n: number;
  win_rate: Nullable<number>;
  place_rate: Nullable<number>;
  avg_finish: Nullable<number>;
  by_finish?: {
    prev_finish: Nullable<number>;
    n: number;
    place_rate: Nullable<number>;
    avg_finish: Nullable<number>;
  };
}

export interface RaceAnalysisHorseOutput {
  name: string;
  jockey: Nullable<string>;
  sire: Nullable<string>;
  rotation: RaceAnalysisFactorScore;
  sire_fit: RaceAnalysisFactorScore;
  jockey_fit: RaceAnalysisFactorScore;
  horse_form: {
    total: number;
    wins: number;
    places: number;
    win_rate: Nullable<number>;
    place_rate: Nullable<number>;
    avg_finish: Nullable<number>;
    same_cond: {
      n: number;
      place_rate: Nullable<number>;
    };
  };
  composite_score: Nullable<number>;
  factors: string[];
}

export interface RaceAnalysisResponse {
  race_info: {
    venue: string;
    surface: string;
    distance: number;
    race_name: Nullable<string>;
  };
  horses: RaceAnalysisHorseOutput[];
}

// GET /api/backtest-g1 --------------------------------------------------------

export interface BacktestG1ByRace {
  race_name: string;
  year: number;
  top3_horses: Array<{
    name: string;
    score_rank: number;
    actual_finish: Nullable<number>;
  }>;
  hit: boolean;
}

export interface BacktestG1Response {
  total_races: number;
  top1_hit: number;
  top3_hit: number;
  top5_hit: number;
  avg_top3_rank: Nullable<number>;
  roi: {
    top1_win: number;
    top3_place: number;
    trio_box: number;
    top1_bet: number;
    top1_payout: number;
    top3_bet: number;
    top3_payout: number;
    trio_bet: number;
    trio_payout: number;
  };
  by_race: BacktestG1ByRace[];
}

// Static JSON file passthrough endpoints --------------------------------------

export interface StrategiesResponse {
  records: unknown[];
}

export interface WealthCurveResponse {
  // Shape determined by wealth_data.json — kept loose for now.
  [key: string]: unknown;
}

export interface EdgeValidationResponse {
  [key: string]: unknown;
}

export interface AnalyticsResponse {
  [key: string]: unknown;
}

export interface LiveStateResponse {
  state: unknown;
  latest_picks: Nullable<Record<string, unknown>>;
}

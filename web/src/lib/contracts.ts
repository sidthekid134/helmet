export type Trend = "up" | "down" | "flat";
export type Severity = "info" | "warning" | "critical";
export type Health = "healthy" | "degraded" | "unavailable";

export interface ApiEnvelope<T> {
  data: T;
  meta?: {
    generated_at?: string;
    league_id?: string;
  };
}

export interface Player {
  id: string;
  name: string;
  team: string;
  position: string;
  rank?: number;
  value?: number;
  projection?: number;
  trend?: Trend;
  status?: string;
  opponent?: string;
}

export interface SourceHealth {
  id: string;
  name: string;
  status: Health;
  last_synced_at?: string;
  records?: number;
  message?: string;
}

export interface DraftState {
  status: "scheduled" | "active" | "complete";
  current_pick?: number;
  round?: number;
  on_the_clock?: string;
  picks: Array<{
    pick: number;
    team: string;
    player: Player;
    picked_at?: string;
  }>;
  recommendations: Player[];
}

export interface LineupSlot {
  slot: string;
  player: Player | null;
  locked: boolean;
}

export interface Lineup {
  week: number;
  projected_points?: number;
  opponent?: string;
  slots: LineupSlot[];
}

export interface WaiverTarget {
  player: Player;
  priority?: number;
  faab_bid?: number;
  drop_player?: Player;
  rationale?: string;
}

export interface Trade {
  id: string;
  status: "proposed" | "received" | "accepted" | "declined";
  partner: string;
  giving: Player[];
  receiving: Player[];
  value_delta?: number;
  expires_at?: string;
}

export interface Alert {
  id: string;
  title: string;
  body: string;
  severity: Severity;
  created_at: string;
  read: boolean;
}

export interface LearningReview {
  id: string;
  period: string;
  title: string;
  summary?: string;
  outcome?: string;
  lessons: string[];
}

export interface ResearchBrief {
  id: string;
  title: string;
  summary?: string;
  source_count?: number;
  updated_at?: string;
  tags?: string[];
}

export interface LeagueConnection {
  id: string;
  provider: string;
  name: string;
  season: number;
  status: "connected" | "syncing" | "error";
  total_rosters?: number | null;
  default_rounds?: number;
  default_roster_targets?: Record<string, number>;
  default_starters_per_team?: Record<string, number>;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export interface ChatResponse {
  conversation_id: string;
  message: ChatMessage;
}

export interface SetupConnectionInput {
  provider: string;
  league_id: string;
  season: number;
}

export interface DraftPlanSummary {
  id: string;
  league_id?: string | null;
  num_teams: number;
  my_slot: number;
  rounds: number;
  node_count: number;
  status: "active" | "stale" | "superseded";
  seed: number;
  generated_at: string;
}

export interface DraftPlanPlayer {
  id: string;
  name: string;
  team: string;
  position: string;
}

export interface DraftPlanNode {
  id: string;
  parent_id: string | null;
  depth: number;
  overall_pick: number | null;
  round: number | null;
  chosen_player: DraftPlanPlayer | null;
  chosen_archetype: string | null;
  reach_probability: number;
  roster_player_ids: string[];
  ev: number;
  ev_floor: number;
  ev_ceiling: number;
  rationale: string[];
}

export interface DraftPlanCandidate {
  player: DraftPlanPlayer;
  archetype: string | null;
  survival_probability: number;
  marginal_value: number;
  rank: number;
  expanded: boolean;
  child_node_id: string | null;
}

export interface DraftPlanNodeDetail {
  node: DraftPlanNode;
  candidates: DraftPlanCandidate[];
}

export interface DraftPlanDetail extends DraftPlanNodeDetail {
  plan: DraftPlanSummary;
  created?: boolean;
}

export interface GenerateDraftPlanInput {
  league_id: string;
  my_slot: number;
  num_teams?: number;
  rounds?: number;
  roster_targets?: Record<string, number>;
  starters_per_team?: Record<string, number>;
  simulation_iterations?: number;
  seed?: number;
}

export interface LiveDraftRecommendationsInput {
  my_roster_player_ids: string[];
  taken_by_others_player_ids: string[];
  limit?: number;
}

export interface LiveDraftRecommendation {
  player: DraftPlanPlayer;
  rank: number;
  score: number;
  vorp: number;
  vona: number;
  survival_to_next: number;
  adp: number;
  projected_points: number;
  urgency: "take_now" | "wait" | "even";
  reasons: string[];
}

export interface LiveDraftRecommendationsResult {
  overall_pick: number | null;
  round: number | null;
  complete: boolean;
  picks_until_next: number;
  starters_per_team: Record<string, number>;
  roster_targets: Record<string, number>;
  recommendations: LiveDraftRecommendation[];
}

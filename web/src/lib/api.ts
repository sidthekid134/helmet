import type {
  Alert,
  ApiEnvelope,
  ChatMessage,
  ChatResponse,
  DraftPlanDetail,
  DraftPlanNodeDetail,
  DraftState,
  GenerateDraftPlanInput,
  LeagueConnection,
  LearningReview,
  LiveDraftRecommendationsInput,
  LiveDraftRecommendationsResult,
  Lineup,
  Player,
  ResearchBrief,
  SetupConnectionInput,
  SourceHealth,
  Trade,
  WaiverTarget,
} from "@/lib/contracts";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function baseUrl() {
  const configured = process.env.NEXT_PUBLIC_API_URL;
  if (!configured) {
    throw new ApiError(
      "API URL is not configured",
      0,
      "Set NEXT_PUBLIC_API_URL to connect Helmet to its API.",
    );
  }
  return configured.replace(/\/$/, "");
}

async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<ApiEnvelope<T>> {
  const response = await fetch(`${baseUrl()}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as
      | { detail?: string; message?: string }
      | null;
    throw new ApiError(
      body?.message ?? `Request failed with status ${response.status}`,
      response.status,
      body?.detail,
    );
  }

  return (await response.json()) as ApiEnvelope<T>;
}

export const api = {
  connections: () => request<LeagueConnection[]>("/v1/connections"),
  connectLeague: (input: SetupConnectionInput) =>
    request<LeagueConnection>("/v1/connections", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  research: () => request<ResearchBrief[]>("/v1/research"),
  sources: () => request<SourceHealth[]>("/v1/sources/health"),
  players: () => request<Player[]>("/v1/players"),
  draft: () => request<DraftState>("/v1/draft"),
  generateDraftPlan: (input: GenerateDraftPlanInput) =>
    request<DraftPlanDetail>("/v1/draft/plan", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  draftPlan: (planId: string) => request<DraftPlanDetail>(`/v1/draft/plan/${planId}`),
  draftPlanNode: (planId: string, nodeId: string) =>
    request<DraftPlanNodeDetail>(`/v1/draft/plan/${planId}/nodes/${nodeId}`),
  liveDraftRecommendations: (planId: string, input: LiveDraftRecommendationsInput) =>
    request<LiveDraftRecommendationsResult>(`/v1/draft/plan/${planId}/live-recommendations`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  lineup: () => request<Lineup>("/v1/lineup"),
  waivers: () => request<WaiverTarget[]>("/v1/waivers"),
  trades: () => request<Trade[]>("/v1/trades"),
  alerts: () => request<Alert[]>("/v1/alerts"),
  learning: () => request<LearningReview[]>("/v1/learning/reviews"),
  chatHistory: () => request<ChatMessage[]>("/v1/chat/messages"),
  sendMessage: (message: string) =>
    request<ChatResponse>("/v1/chat/messages", {
      method: "POST",
      body: JSON.stringify({ message }),
    }),
};

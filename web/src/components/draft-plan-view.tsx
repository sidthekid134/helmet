"use client";

import {
  Ban,
  Check,
  ChevronRight,
  GitBranch,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  Search,
  Sparkles,
  Undo2,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import type {
  DraftPlanCandidate,
  DraftPlanDetail,
  DraftPlanNodeDetail,
  DraftPlanPlayer,
  LeagueConnection,
  LiveDraftRecommendation,
  LiveDraftRecommendationsResult,
} from "@/lib/contracts";
import { useApi } from "@/hooks/use-api";
import { EmptyState } from "@/components/states";

const POSITIONS = ["QB", "RB", "WR", "TE"] as const;
const URGENCY_LABEL: Record<LiveDraftRecommendation["urgency"], string> = {
  take_now: "Take now",
  wait: "Wait",
  even: "Even",
};

interface FormState {
  leagueId: string;
  numTeams: number;
  mySlot: number;
  rounds: number;
  rosterTargets: Record<string, number>;
  startersPerTeam: Record<string, number>;
}

// Only used until a connected league is picked, at which point the form
// switches to that league's own total_rosters / roster_positions-derived
// shape (see `applyConnectionDefaults`). Sending back exactly what the
// backend derived is what lets a plan generated from unmodified defaults
// land on the same content hash `helmet research warm-start` precomputed.
const DEFAULT_FORM: FormState = {
  leagueId: "",
  numTeams: 12,
  mySlot: 1,
  rounds: 15,
  rosterTargets: { QB: 2, RB: 5, WR: 6, TE: 2 },
  startersPerTeam: { QB: 1, RB: 2, WR: 2, TE: 1 },
};

function applyConnectionDefaults(form: FormState, connection: LeagueConnection | undefined): FormState {
  if (!connection) return form;
  return {
    ...form,
    numTeams: connection.total_rosters ?? form.numTeams,
    rounds: connection.default_rounds ?? form.rounds,
    rosterTargets: connection.default_roster_targets ?? form.rosterTargets,
    startersPerTeam: connection.default_starters_per_team ?? form.startersPerTeam,
  };
}

export function DraftPlanView() {
  const connections = useApi(api.connections);
  const [form, setForm] = useState<FormState>(DEFAULT_FORM);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<Error | null>(null);
  const [plan, setPlan] = useState<DraftPlanDetail | null>(null);
  const [steps, setSteps] = useState<DraftPlanNodeDetail[]>([]);
  const [navError, setNavError] = useState<Error | null>(null);
  const [navigating, setNavigating] = useState<string | null>(null);
  // Players someone else has already drafted, keyed by player id -> name.
  // Helmet has no live Sleeper draft feed yet, so for now the user marks
  // opponent picks by hand as they happen; this hides those players from
  // every candidate list (across all nodes) so the modeled options stay
  // honest even when reality diverges from the precomputed tree.
  const [takenByOthers, setTakenByOthers] = useState<Record<string, string>>({});
  // Actual live-draft roster. The prep tree below is hypothetical and must
  // not advance "your next pick" when you click through a simulated branch.
  const [myLivePicks, setMyLivePicks] = useState<DraftPlanPlayer[]>([]);

  const current = steps[steps.length - 1] ?? null;

  function markTakenPlayer(player: DraftPlanPlayer) {
    setTakenByOthers((prev) => ({ ...prev, [player.id]: player.name }));
  }

  function markTaken(candidate: DraftPlanCandidate) {
    markTakenPlayer(candidate.player);
  }

  function unmarkTaken(playerId: string) {
    setTakenByOthers((prev) => {
      const next = { ...prev };
      delete next[playerId];
      return next;
    });
  }

  function draftMine(player: DraftPlanPlayer) {
    setMyLivePicks((prev) => (prev.some((p) => p.id === player.id) ? prev : [...prev, player]));
  }

  function undoMine(playerId: string) {
    setMyLivePicks((prev) => prev.filter((p) => p.id !== playerId));
  }

  async function generate(event: FormEvent) {
    event.preventDefault();
    setGenerating(true);
    setGenerateError(null);
    try {
      const response = await api.generateDraftPlan({
        league_id: form.leagueId,
        num_teams: form.numTeams,
        my_slot: form.mySlot,
        rounds: form.rounds,
        roster_targets: form.rosterTargets,
        starters_per_team: form.startersPerTeam,
      });
      setPlan(response.data);
      setSteps([{ node: response.data.node, candidates: response.data.candidates }]);
    } catch (error) {
      setGenerateError(error instanceof Error ? error : new Error("Could not generate plan"));
    } finally {
      setGenerating(false);
    }
  }

  async function openCandidate(candidate: DraftPlanCandidate) {
    if (!plan || !candidate.expanded || !candidate.child_node_id) return;
    setNavigating(candidate.child_node_id);
    setNavError(null);
    try {
      const response = await api.draftPlanNode(plan.plan.id, candidate.child_node_id);
      setSteps((prev) => [...prev, response.data]);
    } catch (error) {
      setNavError(error instanceof Error ? error : new Error("Could not load that pick"));
    } finally {
      setNavigating(null);
    }
  }

  function jumpTo(index: number) {
    setSteps((prev) => prev.slice(0, index + 1));
  }

  function reset() {
    setPlan(null);
    setSteps([]);
    setGenerateError(null);
    setNavError(null);
    setTakenByOthers({});
    setMyLivePicks([]);
  }

  return (
    <>
      <div className="page-header">
        <div>
          <span className="eyebrow">Draft-day aid</span>
          <h1>Draft plan</h1>
          <p>
            Log picks as they happen. Helmet re-ranks who is left and tells you why the next
            pick is that one.
          </p>
        </div>
        {plan && (
          <div className="page-actions">
            <button className="secondary-button" onClick={reset}>
              <RotateCcw size={15} />
              Start over
            </button>
          </div>
        )}
      </div>

      {!plan ? (
        <PlanForm
          form={form}
          setForm={setForm}
          onSubmit={generate}
          submitting={generating}
          error={generateError}
          connections={connections}
        />
      ) : (
        <>
          <LiveDraftBoard
            planId={plan.plan.id}
            myLivePicks={myLivePicks}
            takenByOthers={takenByOthers}
            onDraftMine={draftMine}
            onUndoMine={undoMine}
            onMarkOthers={markTakenPlayer}
            onUnmarkOthers={unmarkTaken}
          />
          <details className="prep-tree">
            <summary>
              <GitBranch size={14} />
              Prep tree
              <span>Explore hypothetical branches without changing the live board.</span>
            </summary>
            <PlanExplorer
              plan={plan.plan}
              steps={steps}
              current={current}
              navigating={navigating}
              navError={navError}
              takenByOthers={takenByOthers}
              onOpenCandidate={openCandidate}
              onJumpTo={jumpTo}
              onMarkTaken={markTaken}
              onUnmarkTaken={unmarkTaken}
            />
          </details>
        </>
      )}
    </>
  );
}

function PlanForm({
  form,
  setForm,
  onSubmit,
  submitting,
  error,
  connections,
}: {
  form: FormState;
  setForm: (updater: (prev: FormState) => FormState) => void;
  onSubmit: (event: FormEvent) => void;
  submitting: boolean;
  error: Error | null;
  connections: ReturnType<typeof useApi<import("@/lib/contracts").LeagueConnection[]>>;
}) {
  function updatePositionMap(key: "rosterTargets" | "startersPerTeam", position: string, value: number) {
    setForm((prev) => ({ ...prev, [key]: { ...prev[key], [position]: value } }));
  }

  return (
    <div className="setup-grid">
      <section className="panel setup-form-panel">
        <div className="panel-heading">
          <div>
            <h2>Configure this draft</h2>
            <p>Helmet builds projections from your league&apos;s real scoring settings.</p>
          </div>
          <GitBranch size={19} />
        </div>
        <form className="setup-form" onSubmit={onSubmit}>
          <label className="form-field">
            <span>League</span>
            {connections.status === "success" && connections.data.length > 0 ? (
              <select
                required
                value={form.leagueId}
                onChange={(event) => {
                  const id = event.target.value;
                  const connection = connections.data.find((item) => item.id === id);
                  setForm((prev) => applyConnectionDefaults({ ...prev, leagueId: id }, connection));
                }}
              >
                <option value="" disabled>
                  Select a connected league
                </option>
                {connections.data.map((connection) => (
                  <option key={connection.id} value={connection.id}>
                    {connection.name} ({connection.season})
                  </option>
                ))}
              </select>
            ) : (
              <input
                required
                value={form.leagueId}
                onChange={(event) => setForm((prev) => ({ ...prev, leagueId: event.target.value }))}
                placeholder="Connected league ID"
              />
            )}
            <small>
              Teams, rounds, and roster shape below are pulled from your league&apos;s Sleeper
              settings once selected &mdash; override anything before generating.
            </small>
          </label>

          <div className="plan-grid-3">
            <label className="form-field">
              <span>Teams</span>
              <input
                required
                type="number"
                min={2}
                max={32}
                value={form.numTeams}
                onChange={(event) => setForm((prev) => ({ ...prev, numTeams: Number(event.target.value) }))}
              />
            </label>
            <label className="form-field">
              <span>Your slot</span>
              <input
                required
                type="number"
                min={1}
                max={form.numTeams}
                value={form.mySlot}
                onChange={(event) => setForm((prev) => ({ ...prev, mySlot: Number(event.target.value) }))}
              />
            </label>
            <label className="form-field">
              <span>Rounds</span>
              <input
                required
                type="number"
                min={1}
                max={30}
                value={form.rounds}
                onChange={(event) => setForm((prev) => ({ ...prev, rounds: Number(event.target.value) }))}
              />
            </label>
          </div>

          <fieldset>
            <legend>Your target roster depth</legend>
            <div className="plan-grid-4">
              {POSITIONS.map((position) => (
                <label className="form-field" key={position}>
                  <span>{position}</span>
                  <input
                    type="number"
                    min={0}
                    value={form.rosterTargets[position] ?? 0}
                    onChange={(event) =>
                      updatePositionMap("rosterTargets", position, Number(event.target.value))
                    }
                  />
                </label>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend>League starters per team</legend>
            <div className="plan-grid-4">
              {POSITIONS.map((position) => (
                <label className="form-field" key={position}>
                  <span>{position}</span>
                  <input
                    type="number"
                    min={0}
                    value={form.startersPerTeam[position] ?? 0}
                    onChange={(event) =>
                      updatePositionMap("startersPerTeam", position, Number(event.target.value))
                    }
                  />
                </label>
              ))}
            </div>
          </fieldset>

          {error && <div className="inline-error" role="alert">{error.message}</div>}
          <button className="primary-button submit-button" disabled={submitting || !form.leagueId}>
            {submitting ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}
            {submitting ? "Building plan…" : "Generate draft plan"}
            <ChevronRight size={16} />
          </button>
        </form>
      </section>
      <aside className="panel">
        <div className="panel-heading">
          <div>
            <h2>How this works</h2>
            <p>What Helmet does when you generate a plan.</p>
          </div>
        </div>
        <div className="context-card">
          <GitBranch size={18} />
          <div>
            <strong>Simulated opponents</strong>
            <span>
              Rival picks between your turns are simulated from ADP, so every option shows the
              real odds it survives to your next pick.
            </span>
          </div>
        </div>
        <div className="context-card">
          <Sparkles size={18} />
          <div>
            <strong>Precomputed by research</strong>
            <span>
              Every slot for every connected league is already planned out during{" "}
              <code>helmet research warm-start</code>. Generating with the defaults above returns
              that plan instantly instead of rebuilding the tree.
            </span>
          </div>
        </div>
      </aside>
    </div>
  );
}

function LiveDraftBoard({
  planId,
  myLivePicks,
  takenByOthers,
  onDraftMine,
  onUndoMine,
  onMarkOthers,
  onUnmarkOthers,
}: {
  planId: string;
  myLivePicks: DraftPlanPlayer[];
  takenByOthers: Record<string, string>;
  onDraftMine: (player: DraftPlanPlayer) => void;
  onUndoMine: (playerId: string) => void;
  onMarkOthers: (player: DraftPlanPlayer) => void;
  onUnmarkOthers: (playerId: string) => void;
}) {
  const myRosterPlayerIds = useMemo(
    () => myLivePicks.map((player) => player.id),
    [myLivePicks],
  );
  const previousRanks = useRef<Record<string, number>>({});
  const [query, setQuery] = useState("");
  const [positionFilter, setPositionFilter] = useState<(typeof POSITIONS)[number] | "ALL">("ALL");
  const [refreshToken, setRefreshToken] = useState(0);
  const [refreshing, setRefreshing] = useState(true);
  const [rankDeltas, setRankDeltas] = useState<Record<string, number>>({});
  const [movedIds, setMovedIds] = useState<Set<string>>(new Set());
  const [fetchState, setFetchState] = useState<
    | { status: "loading"; data: LiveDraftRecommendationsResult | null }
    | { status: "success"; data: LiveDraftRecommendationsResult }
    | { status: "error"; data: LiveDraftRecommendationsResult | null; error: Error }
  >({ status: "loading", data: null });

  useEffect(() => {
    let cancelled = false;
    const movedTimer: { id?: number } = {};
    api
      .liveDraftRecommendations(planId, {
        my_roster_player_ids: myRosterPlayerIds,
        taken_by_others_player_ids: Object.keys(takenByOthers),
        limit: 80,
      })
      .then((response) => {
        if (cancelled) return;
        const recs = response.data.recommendations;
        const nextDeltas: Record<string, number> = {};
        const moved = new Set<string>();
        if (Object.keys(previousRanks.current).length > 0) {
          for (const rec of recs) {
            const previous = previousRanks.current[rec.player.id];
            if (previous != null && previous !== rec.rank) {
              nextDeltas[rec.player.id] = previous - rec.rank;
              moved.add(rec.player.id);
            }
          }
        }
        previousRanks.current = Object.fromEntries(
          recs.map((rec) => [rec.player.id, rec.rank]),
        );
        setRankDeltas(nextDeltas);
        setMovedIds(moved);
        setFetchState({ status: "success", data: response.data });
        if (moved.size > 0) {
          movedTimer.id = window.setTimeout(() => setMovedIds(new Set()), 900);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setFetchState((prev) => ({
            status: "error",
            data: prev.data,
            error: err instanceof Error ? err : new Error("Could not load live recommendations"),
          }));
        }
      })
      .finally(() => {
        if (!cancelled) setRefreshing(false);
      });
    return () => {
      cancelled = true;
      if (movedTimer.id !== undefined) window.clearTimeout(movedTimer.id);
    };
  }, [planId, myRosterPlayerIds, takenByOthers, refreshToken]);

  const loading = fetchState.status === "loading" && fetchState.data === null;
  const error = fetchState.status === "error" ? fetchState.error : null;
  const data = fetchState.data;
  const complete = data?.complete ?? false;
  const overallPick = data?.overall_pick ?? null;
  const round = data?.round ?? null;
  const picksUntilNext = data?.picks_until_next ?? 0;
  const starters = data?.starters_per_team ?? {};
  const targets = data?.roster_targets ?? {};
  const hiddenIds = useMemo(() => {
    return new Set([...myRosterPlayerIds, ...Object.keys(takenByOthers)]);
  }, [myRosterPlayerIds, takenByOthers]);
  const visible = useMemo(
    () => (data?.recommendations ?? []).filter((row) => !hiddenIds.has(row.player.id)),
    [data, hiddenIds],
  );
  const top = visible[0] ?? null;
  const needle = query.trim().toLowerCase();
  const filtered = visible.filter((row) => {
    if (positionFilter !== "ALL" && row.player.position !== positionFilter) return false;
    if (!needle) return true;
    return (
      row.player.name.toLowerCase().includes(needle) ||
      row.player.team.toLowerCase().includes(needle) ||
      row.player.position.toLowerCase().includes(needle)
    );
  });
  const listRows = filtered.filter((row) => row.player.id !== top?.player.id);

  return (
    <div className="draft-layout">
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Live draft board</h2>
            <p>
              {complete
                ? "Every pick in this plan is logged."
                : overallPick
                  ? `On the clock: overall #${overallPick} (round ${round})${
                      picksUntilNext > 0 ? ` · ${picksUntilNext} picks until you're back` : ""
                    }`
                  : "Re-ranked from every player still on the board."}
            </p>
          </div>
          <button
            type="button"
            className="secondary-button"
            onClick={() => {
              setRefreshing(true);
              setRefreshToken((n) => n + 1);
            }}
          >
            <RefreshCw size={14} className={refreshing ? "spin" : undefined} />
            {refreshing && data ? "Updating…" : "Refresh"}
          </button>
        </div>

        {error && <div className="inline-error" role="alert">{error.message}</div>}

        {!complete && (
        <div className="live-toolbar">
          <label className="field-search">
            <Search size={16} />
            <input
              aria-label="Search available players"
              placeholder="Search to mark taken"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <div className="live-chips">
            <button
              type="button"
              className={positionFilter === "ALL" ? "live-chip active" : "live-chip"}
              onClick={() => setPositionFilter("ALL")}
            >
              All
            </button>
            {POSITIONS.map((position) => (
              <button
                key={position}
                type="button"
                className={positionFilter === position ? "live-chip active" : "live-chip"}
                onClick={() => setPositionFilter(position)}
              >
                {position}
              </button>
            ))}
          </div>
        </div>
        )}

        {complete ? (
          <EmptyState
            title="Draft complete"
            description="Your roster is full for this plan. Undo a pick if you need to keep ranking."
          />
        ) : loading ? (
          <EmptyState title="Loading…" description="Ranking the current board." />
        ) : visible.length === 0 ? (
          <EmptyState
            title="No recommendations yet"
            description="Log a pick, or refresh after the board has players left."
          />
        ) : (
          <>
            {top && (
              <div className="live-pick-highlight">
                <div className="live-pick-body">
                  <span className="eyebrow">Recommended pick</span>
                  <div className="player-cell grow">
                    <span className="player-avatar">{top.player.name.slice(0, 2).toUpperCase()}</span>
                    <div>
                      <strong>{top.player.name}</strong>
                      <span>
                        {top.player.position} · {top.player.team} · ADP {top.adp.toFixed(0)}
                      </span>
                    </div>
                  </div>
                  <ul className="why-list">
                    {top.reasons.slice(0, 4).map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                </div>
                <div className="live-pick-actions">
                  <span className={`urgency-badge ${top.urgency}`}>
                    {URGENCY_LABEL[top.urgency]}
                  </span>
                  <button
                    type="button"
                    className="primary-button"
                    onClick={() => {
                      setRefreshing(true);
                      onDraftMine(top.player);
                    }}
                  >
                    <Check size={14} />I drafted this
                  </button>
                  <button
                    type="button"
                    className="plan-candidate-mark"
                    onClick={() => {
                      setRefreshing(true);
                      onMarkOthers(top.player);
                    }}
                  >
                    <Ban size={12} />
                    Taken
                  </button>
                </div>
              </div>
            )}
            {filtered.length === 0 && visible.length > 0 ? (
              <EmptyState
                title="No players match"
                description="Clear search or switch positions to keep marking the board."
              />
            ) : (
              <div className="plan-candidate-list">
                {listRows.map((recommendation) => {
                  const delta = rankDeltas[recommendation.player.id] ?? 0;
                  const moved = movedIds.has(recommendation.player.id);
                  return (
                    <div
                      key={recommendation.player.id}
                      className={
                        moved
                          ? delta > 0
                            ? "plan-candidate moved-up"
                            : "plan-candidate moved-down"
                          : "plan-candidate"
                      }
                    >
                      <span className="rank">{recommendation.rank}</span>
                      {delta !== 0 && (
                        <span className={delta > 0 ? "rank-delta up" : "rank-delta down"}>
                          {delta > 0 ? `↑${delta}` : `↓${Math.abs(delta)}`}
                        </span>
                      )}
                      <div className="player-cell grow">
                        <span className="player-avatar">
                          {recommendation.player.name.slice(0, 2).toUpperCase()}
                        </span>
                        <div>
                          <strong>{recommendation.player.name}</strong>
                          <span>
                            {recommendation.player.position} · {recommendation.player.team}
                          </span>
                        </div>
                      </div>
                      <div className="plan-candidate-metrics">
                        <span>ADP</span>
                        <strong>{recommendation.adp.toFixed(0)}</strong>
                      </div>
                      <div className="plan-candidate-metrics">
                        <span>VORP</span>
                        <strong>{recommendation.vorp.toFixed(0)}</strong>
                      </div>
                      <div className="plan-candidate-metrics">
                        <span>Survive</span>
                        <strong>{Math.round(recommendation.survival_to_next * 100)}%</strong>
                      </div>
                      <span className={`urgency-badge ${recommendation.urgency}`}>
                        {URGENCY_LABEL[recommendation.urgency]}
                      </span>
                      <button
                        type="button"
                        className="plan-candidate-mark undo"
                        onClick={() => {
                          setRefreshing(true);
                          onDraftMine(recommendation.player);
                        }}
                      >
                        <Check size={12} />
                        Mine
                      </button>
                      <button
                        type="button"
                        className="plan-candidate-mark"
                        onClick={() => {
                          setRefreshing(true);
                          onMarkOthers(recommendation.player);
                        }}
                      >
                        <Ban size={12} />
                        Taken
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}
      </section>

      <aside className="panel accent-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">
              <Sparkles size={13} /> Draft log
            </span>
            <h2>{overallPick ? `Pick #${overallPick}` : complete ? "Complete" : "Draft log"}</h2>
          </div>
        </div>

        <RosterSlotGrid picks={myLivePicks} starters={starters} targets={targets} />

        <div className="panel-heading">
          <div>
            <h2>My roster ({myLivePicks.length})</h2>
          </div>
        </div>
        {myLivePicks.length === 0 ? (
          <p className="muted">No picks logged here yet.</p>
        ) : (
          <div className="plan-candidate-list">
            {myLivePicks.map((player) => (
              <div key={player.id} className="plan-candidate taken">
                <span className="plan-candidate-taken-label">
                  {player.name} · {player.position}
                </span>
                <button
                  type="button"
                  className="plan-candidate-mark undo"
                  onClick={() => {
                    setRefreshing(true);
                    onUndoMine(player.id);
                  }}
                >
                  <Undo2 size={12} />
                  Undo
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="panel-heading">
          <div>
            <h2>Off the board ({Object.keys(takenByOthers).length})</h2>
          </div>
        </div>
        {Object.keys(takenByOthers).length === 0 ? (
          <p className="muted">No opponent picks logged yet.</p>
        ) : (
          <div className="plan-candidate-list">
            {Object.entries(takenByOthers).map(([playerId, name]) => (
              <div key={playerId} className="plan-candidate taken">
                <span className="plan-candidate-taken-label">{name}</span>
                <button
                  type="button"
                  className="plan-candidate-mark undo"
                  onClick={() => {
                    setRefreshing(true);
                    onUnmarkOthers(playerId);
                  }}
                >
                  <Undo2 size={12} />
                  Undo
                </button>
              </div>
            ))}
          </div>
        )}
      </aside>
    </div>
  );
}

function RosterSlotGrid({
  picks,
  starters,
  targets,
}: {
  picks: DraftPlanPlayer[];
  starters: Record<string, number>;
  targets: Record<string, number>;
}) {
  return (
    <div className="roster-slot-grid">
      {POSITIONS.map((position) => {
        const have = picks.filter((player) => player.position === position);
        const starterCount = starters[position] ?? 0;
        const targetCount = targets[position] ?? starterCount;
        return (
          <div key={position} className={have.length >= starterCount ? "roster-slot filled" : "roster-slot"}>
            <span>{position}</span>
            <strong>
              {have.length}/{starterCount}
            </strong>
            <small>target {targetCount}</small>
            {have.length > 0 && (
              <em>{have.map((player) => player.name.split(" ").slice(-1)[0]).join(", ")}</em>
            )}
          </div>
        );
      })}
    </div>
  );
}

function PlanExplorer({
  plan,
  steps,
  current,
  navigating,
  navError,
  takenByOthers,
  onOpenCandidate,
  onJumpTo,
  onMarkTaken,
  onUnmarkTaken,
}: {
  plan: DraftPlanDetail["plan"];
  steps: DraftPlanNodeDetail[];
  current: DraftPlanNodeDetail | null;
  navigating: string | null;
  navError: Error | null;
  takenByOthers: Record<string, string>;
  onOpenCandidate: (candidate: DraftPlanCandidate) => void;
  onJumpTo: (index: number) => void;
  onMarkTaken: (candidate: DraftPlanCandidate) => void;
  onUnmarkTaken: (playerId: string) => void;
}) {
  if (!current) return <EmptyState title="No plan loaded" description="Generate a plan to start exploring." />;

  return (
    <div className="draft-layout">
      <section className="panel">
        <div className="draft-status">
          <div>
            <span>
              {plan.num_teams} teams · slot {plan.my_slot} · {plan.rounds} rounds · {plan.node_count} nodes
              modeled
            </span>
            <h2>
              {current.node.overall_pick ? `Pick #${current.node.overall_pick}` : "Before your first pick"}
            </h2>
          </div>
          <span className="status-pill healthy">{plan.status}</span>
        </div>

        <PlanBreadcrumb steps={steps} onJumpTo={onJumpTo} />

        <div className="panel-heading">
          <div>
            <h2>Next pick options</h2>
            <p>
              Ranked by projected value; survival is the chance each player is still there. No
              live Sleeper draft feed yet, so mark a player &ldquo;taken&rdquo; here as soon as
              someone else drafts them.
            </p>
          </div>
        </div>

        {navError && <div className="inline-error" role="alert">{navError.message}</div>}

        {current.candidates.length === 0 ? (
          <EmptyState title="No further options" description="This branch has no more modeled picks." />
        ) : (
          <div className="plan-candidate-list">
            {current.candidates.map((candidate) => {
              const takenName = takenByOthers[candidate.player.id];
              // `child_node_id` is null for both "not currently loading" and
              // "never expanded" candidates, and `navigating` defaults to
              // null too -- compare through `expanded` so an idle unexpanded
              // candidate (null vs. null) never reads as "loading".
              const isLoadingThis =
                candidate.expanded && navigating === candidate.child_node_id;
              return (
                <div
                  key={`${candidate.player.id}-${candidate.rank}`}
                  className={
                    takenName
                      ? "plan-candidate taken"
                      : candidate.expanded
                        ? "plan-candidate"
                        : "plan-candidate disabled"
                  }
                >
                  {takenName ? (
                    <span className="plan-candidate-taken-label">
                      <span className="rank">{candidate.rank}</span>
                      {candidate.player.name} &mdash; taken by someone else
                    </span>
                  ) : (
                    <button
                      className="plan-candidate-pick"
                      disabled={!candidate.expanded || isLoadingThis}
                      onClick={() => onOpenCandidate(candidate)}
                    >
                      <span className="rank">{candidate.rank}</span>
                      <div className="player-cell grow">
                        <span className="player-avatar">
                          {candidate.player.name.slice(0, 2).toUpperCase()}
                        </span>
                        <div>
                          <strong>{candidate.player.name}</strong>
                          <span>
                            {candidate.player.position} · {candidate.player.team}
                            {candidate.archetype ? ` · ${candidate.archetype}` : ""}
                          </span>
                        </div>
                      </div>
                      <div className="plan-candidate-metrics">
                        <span>Survival</span>
                        <strong>{Math.round(candidate.survival_probability * 100)}%</strong>
                      </div>
                      <div className="plan-candidate-metrics">
                        <span>VORP</span>
                        <strong>{candidate.marginal_value.toFixed(1)}</strong>
                      </div>
                      {isLoadingThis ? (
                        <LoaderCircle className="spin" size={16} />
                      ) : candidate.expanded ? (
                        <ChevronRight size={16} />
                      ) : (
                        <span className="muted">Not explored</span>
                      )}
                    </button>
                  )}
                  {takenName ? (
                    <button
                      className="plan-candidate-mark undo"
                      onClick={() => onUnmarkTaken(candidate.player.id)}
                    >
                      <Undo2 size={12} />
                      Undo
                    </button>
                  ) : (
                    <button className="plan-candidate-mark" onClick={() => onMarkTaken(candidate)}>
                      <Ban size={12} />
                      Taken
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>

      <aside className="panel accent-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">
              <Sparkles size={13} /> This roster so far
            </span>
            <h2>{current.node.roster_player_ids.length} players drafted</h2>
          </div>
        </div>
        <div className="plan-ev-grid">
          <div>
            <span>Projected EV</span>
            <strong>{current.node.ev.toFixed(0)}</strong>
          </div>
          <div>
            <span>Floor</span>
            <strong>{current.node.ev_floor.toFixed(0)}</strong>
          </div>
          <div>
            <span>Ceiling</span>
            <strong>{current.node.ev_ceiling.toFixed(0)}</strong>
          </div>
        </div>
        <div className="plan-ev-grid">
          <div>
            <span>Reach probability</span>
            <strong>{Math.round(current.node.reach_probability * 100)}%</strong>
          </div>
        </div>
        {current.node.rationale.length > 0 && (
          <div className="tag-row">
            {current.node.rationale.map((item) => (
              <span key={item}>{item}</span>
            ))}
          </div>
        )}
      </aside>
    </div>
  );
}

function PlanBreadcrumb({
  steps,
  onJumpTo,
}: {
  steps: DraftPlanNodeDetail[];
  onJumpTo: (index: number) => void;
}) {
  return (
    <div className="plan-breadcrumb">
      {steps.map((step, index) => (
        <span key={step.node.id}>
          {index > 0 && <ChevronRight size={12} />}
          <button onClick={() => onJumpTo(index)} disabled={index === steps.length - 1}>
            {step.node.chosen_player ? step.node.chosen_player.name : "Start"}
          </button>
        </span>
      ))}
    </div>
  );
}
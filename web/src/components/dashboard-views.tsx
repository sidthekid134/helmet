"use client";

import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  CalendarDays,
  CheckCircle2,
  CircleDot,
  Clock3,
  Filter,
  Plus,
  RefreshCw,
  Search,
  SlidersHorizontal,
  Sparkles,
  Target,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  Alert,
  DraftState,
  LearningReview,
  Lineup,
  Player,
  ResearchBrief,
  SourceHealth,
  Trade,
  WaiverTarget,
} from "@/lib/contracts";
import { useApi } from "@/hooks/use-api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";

export type Surface =
  | "board"
  | "research"
  | "plan"
  | "draft"
  | "lineup"
  | "waivers"
  | "trades"
  | "alerts"
  | "learning";

const copy: Record<Surface, { eyebrow: string; title: string; description: string }> = {
  board: { eyebrow: "Intelligence workspace", title: "Player board", description: "A unified view of player value, projection, movement, and availability." },
  research: { eyebrow: "Data operations", title: "Research & source health", description: "Monitor intelligence inputs and review the latest synthesized research." },
  plan: { eyebrow: "Pre-draft strategy", title: "Draft plan", description: "Explore a simulated draft tree before the clock starts." },
  draft: { eyebrow: "Draft room", title: "Live draft", description: "Track the room, your roster shape, and the best options on the board." },
  lineup: { eyebrow: "Week management", title: "Weekly lineup", description: "Review every slot, matchup, and projected decision for the week." },
  waivers: { eyebrow: "Roster moves", title: "Waiver wire", description: "Prioritize additions using team fit, opportunity, and expected cost." },
  trades: { eyebrow: "Roster strategy", title: "Trade center", description: "Evaluate active proposals and find value-aligned roster improvements." },
  alerts: { eyebrow: "Decision feed", title: "Alerts", description: "Injuries, role changes, market movement, and deadlines that need attention." },
  learning: { eyebrow: "Continuous improvement", title: "Learning review", description: "Turn past decisions and outcomes into a stronger weekly process." },
};

function PageHeader({ surface, action }: { surface: Surface; action?: React.ReactNode }) {
  const item = copy[surface];
  return (
    <div className="page-header">
      <div><span className="eyebrow">{item.eyebrow}</span><h1>{item.title}</h1><p>{item.description}</p></div>
      {action && <div className="page-actions">{action}</div>}
    </div>
  );
}

function Toolbar({ placeholder = "Search players" }: { placeholder?: string }) {
  return (
    <div className="toolbar">
      <label className="field-search"><Search size={16} /><input aria-label={placeholder} placeholder={placeholder} /></label>
      <button className="secondary-button"><Filter size={15} />Filter</button>
      <button className="icon-button"><SlidersHorizontal size={16} /></button>
    </div>
  );
}

function PlayerTable({ players }: { players: Player[] }) {
  return (
    <div className="table-scroll">
      <table>
        <thead><tr><th>Rank</th><th>Player</th><th>Position</th><th>Team</th><th>Projection</th><th>Value</th><th>Trend</th></tr></thead>
        <tbody>{players.map((player, index) => (
          <tr key={player.id}>
            <td className="rank">{player.rank ?? index + 1}</td>
            <td><div className="player-cell"><span className="player-avatar">{player.name.slice(0, 2).toUpperCase()}</span><div><strong>{player.name}</strong><span>{player.status ?? "No status"}</span></div></div></td>
            <td><span className="position-chip">{player.position}</span></td>
            <td>{player.team}</td><td>{player.projection ?? "—"}</td><td className="value-cell">{player.value ?? "—"}</td>
            <td>{player.trend === "up" ? <span className="trend up"><ArrowUpRight size={14} /> Rising</span> : player.trend === "down" ? <span className="trend down"><ArrowDownRight size={14} /> Falling</span> : <span className="muted">Steady</span>}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

export function BoardView() {
  const state = useApi(api.players);
  return <><PageHeader surface="board" action={<><button className="secondary-button"><RefreshCw size={15} />Refresh</button><button className="primary-button"><Plus size={15} />Add watchlist</button></>} /><div className="metric-grid"><Metric label="Players tracked" value={state.status === "success" ? String(state.data.length) : "—"} note="Current player pool" /><Metric label="Roster needs" value="—" note="Connect a league to calculate" /><Metric label="Market movers" value="—" note="Awaiting source data" /><Metric label="Next kickoff" value="—" note="Schedule not available" /></div><section className="panel"><div className="panel-heading"><div><h2>Consensus board</h2><p>Ranked across your configured sources and league context.</p></div></div><Toolbar />{state.status === "loading" ? <LoadingState label="Building player board" /> : state.status === "error" ? <ErrorState error={state.error} retry={state.retry} /> : state.data.length === 0 ? <EmptyState title="No players available" description="Player data will appear after your sources complete their first sync." /> : <PlayerTable players={state.data} />}</section></>;
}

function Metric({ label, value, note }: { label: string; value: string; note: string }) {
  return <div className="metric-card"><span>{label}</span><strong>{value}</strong><small>{note}</small></div>;
}

function SourcePanel() {
  const state = useApi(api.sources);
  if (state.status === "loading") return <LoadingState label="Checking source health" />;
  if (state.status === "error") return <ErrorState error={state.error} retry={state.retry} />;
  if (state.data.length === 0) return <EmptyState title="No sources configured" description="Connect research providers to start collecting player intelligence." />;
  return <div className="source-list">{state.data.map((source: SourceHealth) => <div className="source-row" key={source.id}><span className={`health-dot ${source.status}`} /><div><strong>{source.name}</strong><span>{source.message ?? (source.last_synced_at ? `Last sync ${new Date(source.last_synced_at).toLocaleString()}` : "Never synced")}</span></div><span className={`status-pill ${source.status}`}>{source.status}</span></div>)}</div>;
}

function ResearchPanel() {
  const state = useApi(api.research);
  if (state.status === "loading") return <LoadingState label="Loading research briefs" />;
  if (state.status === "error") return <ErrorState error={state.error} retry={state.retry} />;
  if (state.data.length === 0) return <EmptyState title="No research briefs yet" description="Briefs will appear when connected sources produce new intelligence." />;
  return <div className="brief-list">{state.data.map((brief: ResearchBrief) => <article className="brief-card" key={brief.id}><div className="brief-meta"><span>{brief.source_count ?? 0} sources</span><span>{brief.updated_at ? new Date(brief.updated_at).toLocaleDateString() : "No date"}</span></div><h3>{brief.title}</h3><p>{brief.summary ?? "No summary provided."}</p><div className="tag-row">{brief.tags?.map((tag) => <span key={tag}>{tag}</span>)}</div></article>)}</div>;
}

export function ResearchView() {
  return <><PageHeader surface="research" action={<button className="primary-button"><RefreshCw size={15} />Run refresh</button>} /><div className="two-column"><section className="panel"><div className="panel-heading"><div><h2>Source health</h2><p>Connection status and ingestion recency.</p></div><Activity size={18} /></div><SourcePanel /></section><section className="panel wide"><div className="panel-heading"><div><h2>Research briefs</h2><p>Latest synthesized signals from your sources.</p></div></div><ResearchPanel /></section></div></>;
}

export function DraftView() {
  const state = useApi(api.draft);
  return <><PageHeader surface="draft" action={<span className="connection-badge"><CircleDot size={14} />Draft connection</span>} />{state.status === "loading" ? <LoadingState label="Connecting to draft room" /> : state.status === "error" ? <ErrorState error={state.error} retry={state.retry} /> : <DraftContent draft={state.data} />}</>;
}

function DraftContent({ draft }: { draft: DraftState }) {
  if (draft.picks.length === 0 && draft.recommendations.length === 0) return <EmptyState title="Draft room is empty" description="Picks and recommendations will appear when a live draft is connected." />;
  return <div className="draft-layout"><section className="panel"><div className="draft-status"><div><span>Round {draft.round ?? "—"} · Pick {draft.current_pick ?? "—"}</span><h2>{draft.on_the_clock ?? "Waiting for draft"}</h2></div><span className={`status-pill ${draft.status === "active" ? "healthy" : "degraded"}`}>{draft.status}</span></div><div className="panel-heading"><div><h2>Recent picks</h2><p>The latest selections across the room.</p></div></div>{draft.picks.length === 0 ? <EmptyState title="No picks yet" description="Draft selections will stream here." /> : <div className="pick-list">{draft.picks.map((pick) => <div className="pick-row" key={pick.pick}><span>#{pick.pick}</span><div><strong>{pick.player.name}</strong><small>{pick.player.position} · {pick.player.team}</small></div><em>{pick.team}</em></div>)}</div>}</section><section className="panel accent-panel"><div className="panel-heading"><div><span className="eyebrow"><Sparkles size={13} /> Helmet recommends</span><h2>Best available</h2></div></div>{draft.recommendations.length === 0 ? <EmptyState title="No recommendations" description="Recommendations need an active player board and league context." /> : <div className="recommend-list">{draft.recommendations.map((player, index) => <div className="recommend-card" key={player.id}><span>{index + 1}</span><div><strong>{player.name}</strong><small>{player.position} · {player.team}</small></div><b>{player.value ?? "—"}</b></div>)}</div>}</section></div>;
}

export function LineupView() {
  const state = useApi(api.lineup);
  return <><PageHeader surface="lineup" action={<button className="primary-button"><CheckCircle2 size={15} />Review changes</button>} />{state.status === "loading" ? <LoadingState label="Loading weekly lineup" /> : state.status === "error" ? <ErrorState error={state.error} retry={state.retry} /> : state.data.slots.length === 0 ? <EmptyState title="No lineup available" description="Connect a league with an active roster to manage weekly starters." /> : <LineupContent lineup={state.data} />}</>;
}

function LineupContent({ lineup }: { lineup: Lineup }) {
  return <div className="two-column"><section className="panel wide"><div className="panel-heading"><div><h2>Week {lineup.week} starters</h2><p>{lineup.opponent ? `Matchup vs. ${lineup.opponent}` : "Opponent not available"}</p></div><div className="projection"><span>Projected</span><strong>{lineup.projected_points ?? "—"}</strong></div></div><div className="lineup-list">{lineup.slots.map((item, index) => <div className="lineup-row" key={`${item.slot}-${index}`}><span className="slot">{item.slot}</span>{item.player ? <><div className="player-cell"><span className="player-avatar">{item.player.name.slice(0, 2).toUpperCase()}</span><div><strong>{item.player.name}</strong><span>{item.player.team} · {item.player.opponent ?? "No matchup"}</span></div></div><strong className="row-projection">{item.player.projection ?? "—"}</strong></> : <div className="empty-slot">Open roster slot</div>}</div>)}</div></section><aside className="panel"><div className="panel-heading"><div><h2>Week checklist</h2><p>Complete before lineups lock.</p></div></div><div className="check-list"><span><CheckCircle2 size={16} />Review injury designations</span><span><Clock3 size={16} />Confirm game-time decisions</span><span><Target size={16} />Compare flex alternatives</span></div></aside></div>;
}

function ListSurface<T>({ surface, loader, emptyTitle, emptyDescription, render }: { surface: Surface; loader: () => Promise<{ data: T[] }>; emptyTitle: string; emptyDescription: string; render: (item: T) => React.ReactNode }) {
  const state = useApi(loader);
  return <><PageHeader surface={surface} action={<Toolbar placeholder={`Search ${surface}`} />} /><section className="panel">{state.status === "loading" ? <LoadingState /> : state.status === "error" ? <ErrorState error={state.error} retry={state.retry} /> : state.data.length === 0 ? <EmptyState title={emptyTitle} description={emptyDescription} /> : <div className="item-list">{state.data.map(render)}</div>}</section></>;
}

export function WaiversView() {
  return <ListSurface<WaiverTarget> surface="waivers" loader={api.waivers} emptyTitle="No waiver targets yet" emptyDescription="Recommendations will appear after roster needs and the available player pool are synced." render={(item) => <article className="list-row" key={item.player.id}><span className="rank">{item.priority ?? "—"}</span><div className="player-cell grow"><span className="player-avatar">{item.player.name.slice(0, 2).toUpperCase()}</span><div><strong>{item.player.name}</strong><span>{item.player.position} · {item.player.team}</span></div></div><div className="row-detail"><span>Suggested FAAB</span><strong>{item.faab_bid == null ? "—" : `$${item.faab_bid}`}</strong></div><p>{item.rationale ?? "No rationale available."}</p><button className="secondary-button">Review</button></article>} />;
}

export function TradesView() {
  return <ListSurface<Trade> surface="trades" loader={api.trades} emptyTitle="No active trades" emptyDescription="Trade proposals you send or receive will appear here for evaluation." render={(trade) => <article className="trade-card" key={trade.id}><div className="trade-top"><div><span className="eyebrow">With {trade.partner}</span><h3>{trade.giving.length} out · {trade.receiving.length} in</h3></div><span className={`status-pill ${trade.status === "accepted" ? "healthy" : "degraded"}`}>{trade.status}</span></div><div className="trade-sides"><div><span>You give</span><strong>{trade.giving.map((p) => p.name).join(", ") || "Nothing listed"}</strong></div><div><span>You receive</span><strong>{trade.receiving.map((p) => p.name).join(", ") || "Nothing listed"}</strong></div></div><div className="trade-footer"><span>Value delta <strong>{trade.value_delta ?? "—"}</strong></span><button className="secondary-button">Open analysis</button></div></article>} />;
}

export function AlertsView() {
  return <ListSurface<Alert> surface="alerts" loader={api.alerts} emptyTitle="You’re all caught up" emptyDescription="New injury, role, market, and deadline alerts will appear here." render={(alert) => <article className={`alert-row ${alert.read ? "read" : ""}`} key={alert.id}><span className={`severity ${alert.severity}`} /><div className="grow"><div className="alert-title"><strong>{alert.title}</strong><span>{new Date(alert.created_at).toLocaleString()}</span></div><p>{alert.body}</p></div>{!alert.read && <span className="unread-dot" />}</article>} />;
}

export function LearningView() {
  return <ListSurface<LearningReview> surface="learning" loader={api.learning} emptyTitle="No reviews available" emptyDescription="Helmet will create reviews after tracked decisions have measurable outcomes." render={(review) => <article className="learning-card" key={review.id}><div className="learning-date"><CalendarDays size={17} /><span>{review.period}</span></div><div className="grow"><h3>{review.title}</h3><p>{review.summary ?? "No summary available."}</p><div className="lesson-list">{review.lessons.map((lesson) => <span key={lesson}><CheckCircle2 size={14} />{lesson}</span>)}</div></div>{review.outcome && <span className="outcome">{review.outcome}</span>}</article>} />;
}

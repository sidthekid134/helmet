"use client";

import { Check, ChevronRight, Link2, LoaderCircle, ShieldCheck, Unplug } from "lucide-react";
import { FormEvent, useState } from "react";
import { api } from "@/lib/api";
import type { LeagueConnection } from "@/lib/contracts";
import { useApi } from "@/hooks/use-api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";

export function SetupView() {
  const connections = useApi(api.connections);
  const [provider, setProvider] = useState("sleeper");
  const [leagueId, setLeagueId] = useState("");
  const [season, setSeason] = useState(new Date().getFullYear());
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setSubmitError(null);
    try {
      await api.connectLeague({ provider, league_id: leagueId, season });
      setLeagueId("");
      await connections.retry();
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "Connection failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <div className="page-header">
        <div><span className="eyebrow">Workspace setup</span><h1>Connect your fantasy league</h1><p>Give Helmet the context it needs to personalize every draft, lineup, waiver, and trade decision.</p></div>
      </div>
      <div className="setup-steps" aria-label="Setup progress">
        <span className="current"><b>1</b>League</span><i /><span><b>2</b>Sources</span><i /><span><b>3</b>Preferences</span>
      </div>
      <div className="setup-grid">
        <section className="panel setup-form-panel">
          <div className="panel-heading"><div><h2>Add a league</h2><p>Choose a provider and enter its league identifier.</p></div><Link2 size={19} /></div>
          <form className="setup-form" onSubmit={submit}>
            <fieldset>
              <legend>League provider</legend>
              <div className="provider-grid">
                {["sleeper", "espn", "yahoo"].map((item) => (
                  <label className={provider === item ? "provider-card selected" : "provider-card"} key={item}>
                    <input type="radio" name="provider" value={item} checked={provider === item} onChange={() => setProvider(item)} />
                    <span className={`provider-logo ${item}`}>{item.slice(0, 1).toUpperCase()}</span>
                    <strong>{item[0].toUpperCase() + item.slice(1)}</strong>
                    {provider === item && <Check size={15} />}
                  </label>
                ))}
              </div>
            </fieldset>
            <label className="form-field"><span>League ID</span><input required value={leagueId} onChange={(event) => setLeagueId(event.target.value)} placeholder="Enter the ID from your league URL" /><small>Helmet requests read-only league and roster data.</small></label>
            <label className="form-field"><span>Season</span><input required type="number" min="2020" max="2100" value={season} onChange={(event) => setSeason(Number(event.target.value))} /></label>
            {submitError && <div className="inline-error" role="alert">{submitError}</div>}
            <button className="primary-button submit-button" disabled={submitting}>{submitting ? <LoaderCircle className="spin" size={16} /> : <ShieldCheck size={16} />}{submitting ? "Connecting…" : "Connect league"}<ChevronRight size={16} /></button>
          </form>
        </section>
        <aside className="panel">
          <div className="panel-heading"><div><h2>Connected leagues</h2><p>Your active data connections.</p></div></div>
          {connections.status === "loading" ? <LoadingState label="Checking connections" /> : connections.status === "error" ? <ErrorState error={connections.error} retry={connections.retry} /> : connections.data.length === 0 ? <EmptyState title="No leagues connected" description="Use the form to connect your first league." /> : <ConnectionList connections={connections.data} />}
          <div className="privacy-note"><ShieldCheck size={18} /><div><strong>Your data stays yours</strong><span>Credentials are handled by the API and never persisted in this browser.</span></div></div>
        </aside>
      </div>
    </>
  );
}

function ConnectionList({ connections }: { connections: LeagueConnection[] }) {
  return <div className="connection-list">{connections.map((connection) => <div className="connection-row" key={connection.id}><div className="provider-logo">{connection.provider.slice(0, 1).toUpperCase()}</div><div><strong>{connection.name}</strong><span>{connection.provider} · {connection.season}</span></div><span className={`status-pill ${connection.status === "connected" ? "healthy" : "degraded"}`}>{connection.status}</span><button className="icon-button" aria-label={`Disconnect ${connection.name}`}><Unplug size={15} /></button></div>)}</div>;
}

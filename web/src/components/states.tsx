import { AlertTriangle, Inbox, LoaderCircle, RefreshCw } from "lucide-react";

export function LoadingState({ label = "Loading your data" }: { label?: string }) {
  return (
    <div className="state-card" role="status">
      <LoaderCircle className="spin" size={24} />
      <strong>{label}</strong>
      <span>Syncing with Helmet’s data service.</span>
    </div>
  );
}

export function ErrorState({ error, retry }: { error: Error; retry: () => void }) {
  return (
    <div className="state-card error-state" role="alert">
      <AlertTriangle size={24} />
      <strong>We couldn’t load this view</strong>
      <span>{error.message}</span>
      <button className="secondary-button" onClick={retry}><RefreshCw size={15} />Try again</button>
    </div>
  );
}

export function EmptyState({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="state-card">
      <Inbox size={24} />
      <strong>{title}</strong>
      <span>{description}</span>
    </div>
  );
}

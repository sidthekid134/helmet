import { notFound } from "next/navigation";
import {
  AlertsView,
  BoardView,
  DraftView,
  LearningView,
  LineupView,
  ResearchView,
  TradesView,
  WaiversView,
  type Surface,
} from "@/components/dashboard-views";
import { ChatView } from "@/components/chat-view";
import { DraftPlanView } from "@/components/draft-plan-view";
import { SetupView } from "@/components/setup-view";

const views: Record<Surface, React.ComponentType> = {
  board: BoardView,
  research: ResearchView,
  plan: DraftPlanView,
  draft: DraftView,
  lineup: LineupView,
  waivers: WaiversView,
  trades: TradesView,
  alerts: AlertsView,
  learning: LearningView,
};

export function generateStaticParams() {
  return [...Object.keys(views), "setup", "chat"].map((surface) => ({ surface }));
}

export default async function SurfacePage({
  params,
}: {
  params: Promise<{ surface: string }>;
}) {
  const { surface } = await params;
  if (surface === "setup") return <SetupView />;
  if (surface === "chat") return <ChatView />;
  const View = views[surface as Surface];
  if (!View) notFound();
  return <View />;
}

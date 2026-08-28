"use client";

import { ArrowUp, Bot, LoaderCircle, MessageSquareText, Shield, Sparkles } from "lucide-react";
import { FormEvent, useState } from "react";
import { api } from "@/lib/api";
import type { ChatMessage } from "@/lib/contracts";
import { useApi } from "@/hooks/use-api";
import { ErrorState, LoadingState } from "@/components/states";

const prompts = [
  "What should I prioritize this week?",
  "Help me evaluate a trade",
  "Which waiver move fits my roster?",
];

export function ChatView() {
  const history = useApi(api.chatHistory);
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const [localMessages, setLocalMessages] = useState<ChatMessage[]>([]);
  const [sendError, setSendError] = useState<string | null>(null);
  const messages = history.status === "success" ? [...history.data, ...localMessages] : localMessages;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const content = message.trim();
    if (!content || pending) return;
    setPending(true);
    setSendError(null);
    setMessage("");
    const userMessage: ChatMessage = { id: `local-${Date.now()}`, role: "user", content, created_at: new Date().toISOString() };
    setLocalMessages((current) => [...current, userMessage]);
    try {
      const response = await api.sendMessage(content);
      setLocalMessages((current) => [...current, response.data.message]);
    } catch (error) {
      setSendError(error instanceof Error ? error.message : "Message failed");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="chat-layout">
      <aside className="chat-rail">
        <button className="primary-button new-chat"><MessageSquareText size={16} />New conversation</button>
        <div className="chat-history"><span className="nav-label">Recent</span><div className="chat-history-empty">Conversation history will appear here.</div></div>
        <div className="context-card"><Shield size={17} /><div><strong>League-aware answers</strong><span>Helmet uses your connected roster, settings, and research.</span></div></div>
      </aside>
      <section className="chat-main">
        <div className="chat-header"><div><span className="bot-avatar"><Bot size={19} /></span><div><h1>Helmet AI</h1><span><i />Ready to analyze</span></div></div><span className="context-pill">League context</span></div>
        <div className="message-area">
          {history.status === "loading" && messages.length === 0 ? <LoadingState label="Loading conversation" /> : history.status === "error" && messages.length === 0 ? <ErrorState error={history.error} retry={history.retry} /> : messages.length === 0 ? (
            <div className="chat-welcome"><div className="welcome-icon"><Sparkles size={25} /></div><h2>Make the next move with confidence.</h2><p>Ask about your draft, lineup, waivers, trades, or the latest player research. Answers use only connected league and source data.</p><div className="prompt-grid">{prompts.map((prompt) => <button key={prompt} onClick={() => setMessage(prompt)}>{prompt}<ArrowUp size={14} /></button>)}</div></div>
          ) : <div className="messages">{messages.map((item) => <div className={`message ${item.role}`} key={item.id}>{item.role === "assistant" && <span className="bot-avatar"><Bot size={16} /></span>}<div><span>{item.role === "assistant" ? "Helmet" : "You"}</span><p>{item.content}</p></div></div>)}{pending && <div className="message assistant"><span className="bot-avatar"><LoaderCircle className="spin" size={16} /></span><div><span>Helmet</span><p>Analyzing your league context…</p></div></div>}</div>}
        </div>
        <div className="composer-wrap">
          {sendError && <div className="inline-error" role="alert">{sendError}</div>}
          <form className="composer" onSubmit={submit}><textarea aria-label="Message Helmet" rows={1} placeholder="Ask Helmet about your team…" value={message} onChange={(event) => setMessage(event.target.value)} /><button disabled={!message.trim() || pending} aria-label="Send message"><ArrowUp size={18} /></button></form>
          <small>Helmet can make mistakes. Verify important lineup and transaction details.</small>
        </div>
      </section>
    </div>
  );
}

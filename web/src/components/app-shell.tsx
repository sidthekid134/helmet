"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Bell,
  BookOpenCheck,
  Bot,
  CalendarCheck,
  ChevronDown,
  DatabaseZap,
  FlaskConical,
  GitBranch,
  Handshake,
  LayoutDashboard,
  Menu,
  Radio,
  Search,
  Settings2,
  Shield,
  Sparkles,
  Users,
} from "lucide-react";
import { useState } from "react";

const navigation = [
  { href: "/board", label: "Player board", icon: LayoutDashboard },
  { href: "/research", label: "Research", icon: FlaskConical },
  { href: "/plan", label: "Draft plan", icon: GitBranch },
  { href: "/draft", label: "Live draft", icon: Radio },
  { href: "/lineup", label: "Weekly lineup", icon: CalendarCheck },
  { href: "/waivers", label: "Waivers", icon: DatabaseZap },
  { href: "/trades", label: "Trades", icon: Handshake },
  { href: "/alerts", label: "Alerts", icon: Bell },
  { href: "/learning", label: "Learning review", icon: BookOpenCheck },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const active = [...navigation, { href: "/setup", label: "Setup" }].find(
    (item) => pathname === item.href,
  );

  return (
    <div className="app-frame">
      <aside className={`sidebar ${open ? "sidebar-open" : ""}`}>
        <div className="brand">
          <div className="brand-mark"><Shield size={19} strokeWidth={2.4} /></div>
          <div>
            <span className="brand-name">Helmet</span>
            <span className="brand-caption">Fantasy intelligence</span>
          </div>
          <button className="mobile-close" onClick={() => setOpen(false)} aria-label="Close navigation">×</button>
        </div>

        <div className="league-switcher">
          <div className="league-avatar">FL</div>
          <div className="league-copy">
            <span>Current league</span>
            <strong>Connect a league</strong>
          </div>
          <ChevronDown size={15} />
        </div>

        <nav className="nav-list" aria-label="Primary">
          <span className="nav-label">Workspace</span>
          {navigation.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={pathname === href ? "nav-item active" : "nav-item"}
              onClick={() => setOpen(false)}
            >
              <Icon size={18} />
              <span>{label}</span>
              {href === "/draft" && <span className="live-dot" />}
            </Link>
          ))}
        </nav>

        <div className="sidebar-footer">
          <Link href="/setup" className={pathname === "/setup" ? "nav-item active" : "nav-item"}>
            <Settings2 size={18} />
            <span>Setup & connections</span>
          </Link>
          <div className="user-card">
            <div className="user-avatar"><Users size={16} /></div>
            <div><strong>Team manager</strong><span>Helmet workspace</span></div>
          </div>
        </div>
      </aside>

      {open && <button className="sidebar-scrim" aria-label="Close navigation" onClick={() => setOpen(false)} />}

      <div className="main-column">
        <header className="topbar">
          <div className="topbar-left">
            <button className="menu-button" onClick={() => setOpen(true)} aria-label="Open navigation"><Menu size={20} /></button>
            <span className="breadcrumb">Helmet</span><span className="slash">/</span>
            <strong>{pathname === "/chat" ? "AI advisor" : active?.label ?? "Dashboard"}</strong>
          </div>
          <div className="topbar-actions">
            <button className="search-button"><Search size={16} /><span>Search players</span><kbd>⌘ K</kbd></button>
            <Link className="icon-button" href="/alerts" aria-label="Open alerts"><Bell size={18} /></Link>
            <Link className="ai-button" href="/chat"><Sparkles size={16} /><span>Ask Helmet</span></Link>
          </div>
        </header>
        <main className="main-content">{children}</main>
      </div>

      {pathname !== "/chat" && (
        <Link href="/chat" className="floating-ai" aria-label="Open Helmet AI">
          <Bot size={20} /><span>Ask Helmet</span>
        </Link>
      )}
    </div>
  );
}

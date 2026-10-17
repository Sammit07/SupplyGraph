"use client";

import clsx from "clsx";
import {
  BookMarked,
  Boxes,
  DatabaseZap,
  LayoutDashboard,
  Menu,
  Network,
  Radar,
  ShieldAlert,
  Sparkles,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";

import { useMeta } from "@/lib/hooks";

import { AnalystPanel } from "../AnalystPanel";
import { EntityDrawer } from "../EntityDrawer";
import { useAnalyst } from "../providers";
import { CommandSearch } from "./CommandSearch";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/network", label: "Network", icon: Network },
  { href: "/simulator", label: "Impact simulator", icon: Zap },
  { href: "/risks", label: "Risk register", icon: ShieldAlert },
  { href: "/exposure", label: "Exposure", icon: Radar },
  { href: "/data", label: "Data hub", icon: DatabaseZap },
  { href: "/findings", label: "Findings", icon: BookMarked },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [mobileNav, setMobileNav] = useState(false);
  const analyst = useAnalyst();
  const { data: meta } = useMeta();

  if (pathname === "/login") return <>{children}</>;

  const nav = (
    <nav className="flex flex-col gap-0.5">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            onClick={() => setMobileNav(false)}
            className={clsx(
              "group flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition",
              active
                ? "bg-gradient-to-r from-accent/15 to-transparent text-fg"
                : "text-muted hover:bg-ink-800/70 hover:text-fg",
            )}
          >
            <Icon className={clsx("size-4", active ? "text-accent" : "text-faint group-hover:text-muted")} />
            {label}
          </Link>
        );
      })}
    </nav>
  );

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-line bg-ink-950/60 px-3 py-4 backdrop-blur-xl lg:flex">
        <Brand />
        <div className="mt-6">{nav}</div>
        <div className="mt-auto rounded-xl border border-line bg-ink-900/60 p-3 text-xs text-muted">
          <div className="flex items-center gap-2 text-fg">
            <Boxes className="size-3.5 text-accent" /> Graph engine
          </div>
          <p className="mt-1 leading-relaxed">
            Powered by FalkorDB
          </p>
        </div>
      </aside>

      {mobileNav && (
        <div className="fixed inset-0 z-50 bg-ink-950/80 backdrop-blur-sm lg:hidden" onClick={() => setMobileNav(false)}>
          <aside className="h-full w-64 border-r border-line bg-ink-900 px-3 py-4" onClick={(e) => e.stopPropagation()}>
            <Brand />
            <div className="mt-6">{nav}</div>
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-40 flex h-14 items-center gap-3 border-b border-line bg-ink-950/70 px-4 backdrop-blur-xl sm:px-6">
          <button className="btn btn-ghost h-9 px-2 lg:hidden" onClick={() => setMobileNav(true)} aria-label="Open navigation">
            <Menu className="size-5" />
          </button>
          <CommandSearch />
          <div className="ml-auto flex items-center gap-2">
            <WorkspaceSwitcher />
            <button
              className={clsx("btn h-9", analyst.isOpen ? "btn-primary" : "")}
              onClick={() => (analyst.isOpen ? analyst.close() : analyst.open())}
              title={meta && !meta.llm_configured ? "Set OPENAI_API_KEY on the backend to enable the analyst" : undefined}
            >
              <Sparkles className="size-4" />
              <span className="hidden sm:inline">Analyst</span>
            </button>
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1500px] flex-1 px-4 py-6 sm:px-6 lg:px-8">{children}</main>
      </div>

      <EntityDrawer />
      <AnalystPanel />
    </div>
  );
}

function Brand() {
  return (
    <Link href="/" className="flex items-center gap-2.5 px-2">
      <span className="grid size-8 place-items-center rounded-lg bg-gradient-to-br from-accent to-accent-2 shadow-[0_0_24px_-4px_rgb(94_234_212/0.6)]">
        <Network className="size-4.5 text-ink-950" />
      </span>
      <span className="text-[15px] font-semibold tracking-tight">
        Supply<span className="text-accent">Graph</span>
      </span>
    </Link>
  );
}

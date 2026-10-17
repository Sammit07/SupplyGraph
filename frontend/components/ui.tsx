"use client";

import clsx from "clsx";
import { AlertTriangle, ChevronRight, Loader2 } from "lucide-react";
import type { ReactNode } from "react";

import { humanize, kindColor } from "@/lib/format";
import type { EntityRef, Outcome } from "@/lib/types";

import { useInspector } from "./providers";

export function Panel({
  title,
  subtitle,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={clsx("panel animate-fade-in", className)}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 border-b border-line px-5 py-3.5">
          <div className="min-w-0">
            {title && <h2 className="truncate text-sm font-semibold text-fg">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={clsx("p-5", bodyClassName)}>{children}</div>
    </section>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-fg">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Kpi({
  label,
  value,
  hint,
  tone = "default",
  icon,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "default" | "danger" | "warn" | "ok" | "accent";
  icon?: ReactNode;
}) {
  const toneClass = {
    default: "text-fg",
    danger: "text-danger",
    warn: "text-warn",
    ok: "text-ok",
    accent: "bg-gradient-to-r from-accent to-accent-2 bg-clip-text text-transparent",
  }[tone];
  return (
    <div className="panel relative overflow-hidden px-5 py-4">
      <div className="flex items-center justify-between">
        <span className="eyebrow">{label}</span>
        {icon && <span className="text-faint">{icon}</span>}
      </div>
      <div className={clsx("num mt-2 text-3xl font-semibold", toneClass)}>{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={clsx("animate-spin", className ?? "size-4")} aria-hidden />;
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("skeleton", className)} />;
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="flex items-start gap-3 rounded-xl border border-danger/30 bg-danger/5 p-4 text-sm">
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-danger" />
      <div className="min-w-0 flex-1">
        <p className="font-medium text-danger">Something went wrong</p>
        <p className="mt-0.5 break-words text-muted">{message}</p>
      </div>
      {onRetry && (
        <button className="btn h-8" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Empty({
  icon,
  title,
  children,
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      {icon && <div className="mb-1 text-faint">{icon}</div>}
      <p className="text-sm font-medium text-fg">{title}</p>
      {children && <div className="max-w-md text-sm text-muted">{children}</div>}
    </div>
  );
}

const OUTCOME_STYLE: Record<Outcome, string> = {
  answered: "border-ok/30 bg-ok/10 text-ok",
  from_memory: "border-accent-2/30 bg-accent-2/10 text-accent-2",
  not_in_graph: "border-warn/30 bg-warn/10 text-warn",
  unknown_entity: "border-danger/30 bg-danger/10 text-danger",
  ambiguous: "border-warn/30 bg-warn/10 text-warn",
};

export function OutcomeBadge({ outcome }: { outcome: Outcome | null | undefined }) {
  if (!outcome) return null;
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded-md border px-1.5 py-0.5 text-[11px] font-medium",
        OUTCOME_STYLE[outcome],
      )}
    >
      {humanize(outcome)}
    </span>
  );
}

export function Severity({ level }: { level: number }) {
  const color = level >= 4 ? "bg-danger" : level === 3 ? "bg-warn" : "bg-accent";
  return (
    <span className="inline-flex items-center gap-0.5" title={`Severity ${level} of 5`}>
      {[1, 2, 3, 4, 5].map((i) => (
        <span
          key={i}
          className={clsx("h-3 w-1.5 rounded-sm", i <= level ? color : "bg-ink-700")}
        />
      ))}
    </span>
  );
}

export function StatusPill({ status }: { status: string }) {
  const map: Record<string, string> = {
    line_stop_risk: "border-danger/40 bg-danger/10 text-danger",
    disrupted: "border-danger/40 bg-danger/10 text-danger",
    constrained: "border-warn/40 bg-warn/10 text-warn",
    degraded: "border-warn/40 bg-warn/10 text-warn",
    available: "border-ok/40 bg-ok/10 text-ok",
    single_source: "border-danger/40 bg-danger/10 text-danger",
    single_country: "border-warn/40 bg-warn/10 text-warn",
  };
  return (
    <span
      className={clsx(
        "inline-flex shrink-0 items-center rounded-md border px-1.5 py-0.5 text-[11px] font-medium",
        map[status] ?? "border-line bg-ink-800 text-muted",
      )}
    >
      {humanize(status)}
    </span>
  );
}

export function KindDot({ kind, className }: { kind: string; className?: string }) {
  return (
    <span
      className={clsx("inline-block size-2 shrink-0 rounded-full", className)}
      style={{ background: kindColor(kind), boxShadow: `0 0 8px ${kindColor(kind)}66` }}
    />
  );
}

/** Clickable entity reference; opens the inspector drawer. */
export function EntityChip({ entity, className }: { entity: EntityRef; className?: string }) {
  const { open } = useInspector();
  return (
    <button
      type="button"
      onClick={() => open(entity.id)}
      className={clsx("chip hover:border-line-strong hover:bg-ink-700", className)}
      title={`${entity.kind} · ${entity.id}`}
    >
      <KindDot kind={entity.kind} />
      <span className="truncate">{entity.name}</span>
    </button>
  );
}

export function PathChain({ path }: { path: EntityRef[] }) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      {path.map((e, i) => (
        <span key={`${e.id}-${i}`} className="flex items-center gap-1">
          {i > 0 && <ChevronRight className="size-3 text-faint" />}
          <EntityChip entity={e} />
        </span>
      ))}
    </div>
  );
}

export function Bar({
  value,
  max = 1,
  tone = "accent",
}: {
  value: number;
  max?: number;
  tone?: "accent" | "danger" | "warn";
}) {
  const width = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  const color = {
    accent: "from-accent to-accent-2",
    danger: "from-danger to-rose-400",
    warn: "from-warn to-amber-300",
  }[tone];
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-ink-700">
      <div className={clsx("h-full rounded-full bg-gradient-to-r", color)} style={{ width: `${width}%` }} />
    </div>
  );
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { id: T; label: string; icon?: ReactNode }[];
  value: T;
  onChange: (id: T) => void;
}) {
  return (
    <div className="inline-flex rounded-xl border border-line bg-ink-900/70 p-1" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={clsx(
            "inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm transition",
            value === t.id ? "bg-ink-700 text-fg shadow" : "text-muted hover:text-fg",
          )}
        >
          {t.icon}
          {t.label}
        </button>
      ))}
    </div>
  );
}

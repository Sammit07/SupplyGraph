"use client";

import {
  Activity,
  AlertOctagon,
  ArrowRight,
  Boxes,
  CircleDollarSign,
  DatabaseZap,
  Factory,
  Layers,
  RefreshCw,
  Sparkles,
  TriangleAlert,
  Zap,
} from "lucide-react";
import Link from "next/link";

import { useAnalyst } from "@/components/providers";
import {
  Bar,
  Empty,
  EntityChip,
  ErrorBox,
  Kpi,
  KindDot,
  PageHeader,
  Panel,
  Severity,
  Skeleton,
  StatusPill,
} from "@/components/ui";
import { humanize, pct, shortDate } from "@/lib/format";
import { useMeta, useOverview } from "@/lib/hooks";
import type { DisruptionCard } from "@/lib/types";

export function OverviewView() {
  const { data, error, isLoading, mutate, isValidating } = useOverview();
  const { data: meta } = useMeta();
  const analyst = useAnalyst();

  if (error) return <ErrorBox error={error} onRetry={() => mutate()} />;
  if (isLoading || !data) return <OverviewSkeleton />;

  const empty = Object.keys(data.stats.nodes).length === 0;
  if (empty) {
    return (
      <>
        <PageHeader title="Risk overview" />
        <Panel>
          <Empty icon={<DatabaseZap className="size-10" />} title={`Workspace “${meta?.workspace ?? ""}” has no data yet`}>
            Import your item master, bill of materials, approved vendor list and supplier data as CSV,
            or load the sample dataset to explore the product.
            <div className="mt-4">
              <Link className="btn btn-primary" href="/data">
                Open the Data hub <ArrowRight className="size-4" />
              </Link>
            </div>
          </Empty>
        </Panel>
      </>
    );
  }

  const k = data.kpis;
  const share = k.revenue_total > 0 ? k.revenue_at_risk / k.revenue_total : 0;

  return (
    <>
      <PageHeader
        title="Risk overview"
        description="Live picture of every active disruption, propagated through the bill of materials to products, customers and revenue."
        actions={
          <button className="btn" onClick={() => mutate()} disabled={isValidating}>
            <RefreshCw className={isValidating ? "size-4 animate-spin" : "size-4"} /> Refresh
          </button>
        }
      />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <div className="col-span-2 lg:col-span-1">
          <Kpi
            label="Revenue at risk"
            value={k.revenue_at_risk_display}
            tone={k.revenue_at_risk > 0 ? "danger" : "ok"}
            icon={<CircleDollarSign className="size-4" />}
            hint={`${pct(share, 1)} of ${k.revenue_total_display} annual revenue`}
          />
        </div>
        <Kpi label="Active disruptions" value={k.active_disruptions} tone={k.active_disruptions ? "warn" : "ok"} icon={<Activity className="size-4" />} />
        <Kpi label="Products at line-stop risk" value={k.products_at_risk} tone={k.products_at_risk ? "danger" : "ok"} icon={<Factory className="size-4" />} />
        <Kpi label="Suppliers knocked out" value={k.suppliers_disrupted} tone={k.suppliers_disrupted ? "warn" : "ok"} icon={<AlertOctagon className="size-4" />} />
        <Kpi
          label="Single-source parts"
          value={k.single_source_parts}
          icon={<TriangleAlert className="size-4" />}
          hint={`+ ${k.single_country_parts} single-country`}
        />
      </div>

      {data.compound.products.length > 0 && (
        <div className="panel mt-6 border-warn/30 p-5 ring-1 ring-warn/20">
          <div className="flex flex-wrap items-start gap-3">
            <Layers className="mt-0.5 size-5 shrink-0 text-warn" />
            <div className="min-w-0 flex-1">
              <p className="font-semibold text-warn">
                Compound effect: {data.compound.products.length} more product(s) stop because
                disruptions combine ({data.compound.revenue_at_risk_display})
              </p>
              <p className="mt-1 text-sm text-muted">
                No single disruption below stops these products, but together they leave{" "}
                {data.compound.blocked_parts.length > 0 ? (
                  <span className="text-fg">{data.compound.blocked_parts.join(", ")}</span>
                ) : (
                  "some parts"
                )}{" "}
                with no unaffected supplier. This is included in the revenue at risk above.
              </p>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {data.compound.products.map((p) => (
                  <EntityChip key={p.id} entity={{ id: p.id, name: p.name, kind: "Product" }} />
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="mt-6 grid gap-6 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <div className="flex items-center justify-between">
            <h2 className="eyebrow">Disruption board</h2>
            <Link href="/data" className="text-xs text-muted hover:text-fg">
              Add a risk document →
            </Link>
          </div>
          {data.disruptions.length === 0 ? (
            <Panel>
              <Empty icon={<Activity className="size-8" />} title="No active disruptions">
                Upload a supplier notice or risk bulletin in the Data hub, or run a what-if in the simulator.
              </Empty>
            </Panel>
          ) : (
            <div className="grid gap-4 md:grid-cols-2">
              {data.disruptions.map((d) => (
                <DisruptionTile key={d.id} d={d} onAsk={() => analyst.open(`What does ${d.name} put at risk, and which suppliers can cover it?`)} canAsk={Boolean(meta?.llm_configured)} />
              ))}
            </div>
          )}
        </div>

        <div className="space-y-6">
          <Panel title="Critical suppliers" subtitle="Revenue that depends on each supplier alone" actions={<Link className="text-xs text-muted hover:text-fg" href="/risks?tab=criticality">All →</Link>}>
            <ul className="space-y-3">
              {data.critical_suppliers.slice(0, 6).map((s) => (
                <li key={s.id}>
                  <div className="flex items-center gap-2">
                    <EntityChip entity={{ id: s.id, name: s.name, kind: "Supplier" }} />
                    {s.status !== "available" && <StatusPill status={s.status} />}
                    <span className="num ml-auto text-sm">{s.sole_source_revenue_display}</span>
                  </div>
                  <div className="mt-1.5">
                    <Bar value={s.sole_source_revenue} max={data.critical_suppliers[0]?.sole_source_revenue || 1} tone={s.status === "disrupted" ? "danger" : "accent"} />
                  </div>
                </li>
              ))}
            </ul>
          </Panel>

          <Panel title="Single points of failure" actions={<Link className="text-xs text-muted hover:text-fg" href="/risks">All →</Link>}>
            <ul className="divide-y divide-line/60">
              {data.single_points.slice(0, 6).map((p) => (
                <li key={p.id} className="flex items-center gap-3 py-2.5 first:pt-0 last:pb-0">
                  <EntityChip entity={{ id: p.id, name: p.name, kind: "Part" }} />
                  <span className="truncate text-xs text-muted">{p.suppliers.join(", ")}</span>
                  <span className="num ml-auto shrink-0 text-xs text-fg">{p.revenue_exposed_display}</span>
                </li>
              ))}
            </ul>
          </Panel>

          <Panel title="Graph" subtitle={data.stats.graph}>
            <div className="grid grid-cols-2 gap-2">
              {Object.entries(data.stats.nodes)
                .sort((a, b) => b[1] - a[1])
                .map(([kind, n]) => (
                  <div key={kind} className="flex items-center gap-2 rounded-lg border border-line bg-ink-950/40 px-3 py-2">
                    <KindDot kind={kind} />
                    <span className="text-xs text-muted">{kind}</span>
                    <span className="num ml-auto text-sm">{n}</span>
                  </div>
                ))}
            </div>
            <p className="mt-3 flex items-center gap-1.5 text-xs text-faint">
              <Boxes className="size-3.5" />
              {Object.values(data.stats.relationships).reduce((a, b) => a + b, 0)} relationships ·{" "}
              {data.stats.documents} documents · {data.stats.findings} findings
            </p>
          </Panel>
        </div>
      </div>
    </>
  );
}

function DisruptionTile({ d, onAsk, canAsk }: { d: DisruptionCard; onAsk: () => void; canAsk: boolean }) {
  const critical = d.line_stop_products.length > 0;
  return (
    <div className={`panel panel-hover relative overflow-hidden p-5 ${critical ? "ring-1 ring-danger/25" : ""}`}>
      {critical && <div className="pointer-events-none absolute -right-16 -top-16 size-40 rounded-full bg-danger/10 blur-3xl" />}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs text-muted">
            <span className="relative flex size-2">
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-danger/60" />
              <span className="relative inline-flex size-2 rounded-full bg-danger" />
            </span>
            {humanize(d.category)} · since {shortDate(d.started_at)}
          </div>
          <EntityChipButton id={d.id} name={d.name} />
        </div>
        <Severity level={d.severity} />
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {d.affects.map((a) => (
          <EntityChip key={a.id} entity={a} />
        ))}
      </div>

      <div className="mt-4 grid grid-cols-3 gap-3 border-t border-line pt-4">
        <Metric label="At risk alone" value={d.revenue_at_risk_display} tone={d.revenue_at_risk > 0 ? "text-danger" : "text-fg"} />
        <Metric label="Suppliers hit" value={String(d.suppliers_hit)} hint={d.suppliers_degraded ? `+${d.suppliers_degraded} degraded` : undefined} />
        <Metric label="Expected end" value={d.expected_end ? shortDate(d.expected_end) : "Unknown"} hint={d.outage_days != null ? `${d.outage_days} days left` : undefined} />
      </div>

      {(d.line_stop_products.length > 0 || d.constrained_products.length > 0) && (
        <div className="mt-4 space-y-1.5 text-xs">
          {d.line_stop_products.length > 0 && (
            <p>
              <span className="text-danger">Line-stop risk:</span> <span className="text-fg">{d.line_stop_products.join(", ")}</span>
            </p>
          )}
          {d.constrained_products.length > 0 && (
            <p>
              <span className="text-warn">Constrained:</span> <span className="text-muted">{d.constrained_products.join(", ")}</span>
            </p>
          )}
          {d.stockouts.length > 0 && (
            <p className="flex items-center gap-1 text-danger">
              <TriangleAlert className="size-3.5" /> Inventory runs out before recovery: {d.stockouts.join(", ")}
            </p>
          )}
        </div>
      )}

      <div className="mt-4 flex gap-2">
        <Link className="btn h-8" href={`/simulator?entity=${encodeURIComponent(d.id)}`}>
          <Zap className="size-3.5" /> Full impact
        </Link>
        {canAsk && (
          <button className="btn h-8" onClick={onAsk}>
            <Sparkles className="size-3.5" /> Ask analyst
          </button>
        )}
      </div>
    </div>
  );
}

function EntityChipButton({ id, name }: { id: string; name: string }) {
  return (
    <div className="mt-1.5">
      <EntityChip entity={{ id, name, kind: "Disruption" }} className="!border-transparent !bg-transparent !px-0 text-base font-semibold" />
    </div>
  );
}

function Metric({ label, value, hint, tone = "text-fg" }: { label: string; value: string; hint?: string; tone?: string }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] text-faint">{label}</p>
      <p className={`num mt-0.5 truncate text-sm font-semibold ${tone}`}>{value}</p>
      {hint && <p className="truncate text-[11px] text-muted">{hint}</p>}
    </div>
  );
}

function OverviewSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-8 w-64" />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-28" />
        ))}
      </div>
      <div className="grid gap-6 xl:grid-cols-3">
        <Skeleton className="h-96 xl:col-span-2" />
        <Skeleton className="h-96" />
      </div>
    </div>
  );
}

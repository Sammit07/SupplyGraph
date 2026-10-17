"use client";

import { Clock, Crown, ShieldAlert } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { Bar as RBar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import useSWR from "swr";

import { EntityPicker } from "@/components/EntityPicker";
import { Bar, Empty, EntityChip, ErrorBox, OutcomeBadge, PageHeader, Panel, Skeleton, StatusPill, Tabs } from "@/components/ui";
import { runTool } from "@/lib/api";
import { money } from "@/lib/format";
import { useMeta } from "@/lib/hooks";
import type { CriticalSupplier, EntityRef, SinglePoint, ToolResult } from "@/lib/types";

type Tab = "spof" | "criticality" | "leadtime";

const toolFetcher = <D,>([name, args]: [string, Record<string, unknown>]) => runTool<D>(name, args);

export function RisksView() {
  const params = useSearchParams();
  const router = useRouter();
  const tab = (params.get("tab") as Tab) || "spof";
  const setTab = (t: Tab) => router.replace(`/risks?tab=${t}`);

  return (
    <>
      <PageHeader
        title="Risk register"
        description="Structural risk in the network, independent of today’s disruptions: concentration, critical suppliers and the slowest sourcing chains."
        actions={
          <Tabs<Tab>
            value={tab}
            onChange={setTab}
            tabs={[
              { id: "spof", label: "Single points", icon: <ShieldAlert className="size-4" /> },
              { id: "criticality", label: "Supplier criticality", icon: <Crown className="size-4" /> },
              { id: "leadtime", label: "Lead time", icon: <Clock className="size-4" /> },
            ]}
          />
        }
      />
      {tab === "spof" && <SinglePoints />}
      {tab === "criticality" && <Criticality />}
      {tab === "leadtime" && <LeadTime />}
    </>
  );
}

function SinglePoints() {
  const [product, setProduct] = useState<EntityRef | null>(null);
  const { data, error, isLoading } = useSWR<ToolResult<{ parts: SinglePoint[]; total: number; single_source_total: number; single_country_total: number }>>(
    ["single_points_of_failure", { limit: 50, ...(product ? { product: product.id } : {}) }],
    toolFetcher<{ parts: SinglePoint[]; total: number; single_source_total: number; single_country_total: number }>,
  );
  const parts = data?.data.parts ?? [];
  const max = Math.max(1, ...parts.map((p) => p.revenue_exposed));
  return (
    <Panel
      title="Concentrated parts"
      subtitle={
        data?.data.total
          ? `${data.data.single_source_total} single-source and ${data.data.single_country_total} single-country parts` +
            (data.data.total > parts.length ? ` (showing the top ${parts.length} by downstream revenue)` : "")
          : "Parts with a single qualified supplier, or all suppliers in one country"
      }
      actions={<EntityPicker className="w-72" kinds={["Product"]} value={product} onChange={setProduct} placeholder="All products" />}
      bodyClassName="p-0"
    >
      {error && <div className="p-5"><ErrorBox error={error} /></div>}
      {isLoading && <div className="space-y-2 p-5">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10" />)}</div>}
      {data && parts.length === 0 && <Empty title="No concentrated parts">{data.summary}</Empty>}
      {parts.length > 0 && (
        <div className="overflow-x-auto">
          <table className="table-base">
            <thead>
              <tr>
                <th>Part</th>
                <th>Risk</th>
                <th>Suppliers</th>
                <th>Countries</th>
                <th>On hand</th>
                <th>Feeds</th>
                <th className="w-48">Revenue downstream</th>
              </tr>
            </thead>
            <tbody>
              {parts.map((p) => (
                <tr key={p.id}>
                  <td><EntityChip entity={{ id: p.id, name: p.name, kind: "Part" }} /></td>
                  <td><StatusPill status={p.risk} /></td>
                  <td className="text-xs text-muted">{p.suppliers.join(", ")}</td>
                  <td className="text-xs">{p.countries.join(", ")}</td>
                  <td className="num text-xs text-muted">{p.inventory_days}d</td>
                  <td className="text-xs text-muted">{p.products.join(", ")}</td>
                  <td>
                    <div className="flex items-center gap-2">
                      <span className="num w-16 text-xs">{p.revenue_exposed_display}</span>
                      <Bar value={p.revenue_exposed} max={max} tone={p.risk === "single_source" ? "danger" : "warn"} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function Criticality() {
  const { data: meta } = useMeta();
  const { data, error, isLoading } = useSWR<ToolResult<{ suppliers: CriticalSupplier[]; currency: string }>>(
    ["supplier_criticality", { limit: 20 }],
    toolFetcher<{ suppliers: CriticalSupplier[]; currency: string }>,
  );
  const rows = data?.data.suppliers ?? [];
  const currency = data?.data.currency ?? meta?.currency ?? "USD";
  const chart = rows.slice(0, 12).map((r) => ({
    name: r.name.length > 22 ? `${r.name.slice(0, 21)}…` : r.name,
    "Sole-sourced": r.sole_source_revenue,
    "Total reach": r.revenue_reach,
  }));

  if (error) return <ErrorBox error={error} />;
  if (isLoading || !data) return <Skeleton className="h-96" />;
  return (
    <div className="grid gap-6 2xl:grid-cols-2">
      <Panel title="Revenue dependence" subtitle="Sole-sourced revenue vs. everything the supplier feeds">
        <div className="h-[420px]">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chart} layout="vertical" margin={{ left: 8, right: 16 }}>
              <CartesianGrid horizontal={false} stroke="rgba(148,163,184,0.1)" />
              <XAxis type="number" tickFormatter={(v) => money(v, currency)} stroke="#5b6780" fontSize={11} />
              <YAxis type="category" dataKey="name" width={150} stroke="#8b97ad" fontSize={11} />
              <Tooltip
                cursor={{ fill: "rgba(148,163,184,0.06)" }}
                contentStyle={{ background: "#0a1120", border: "1px solid rgba(148,163,184,0.2)", borderRadius: 10, fontSize: 12 }}
                formatter={(v) => money(Number(v), currency)}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <RBar dataKey="Total reach" fill="#818cf8" fillOpacity={0.35} radius={[0, 4, 4, 0]} />
              <RBar dataKey="Sole-sourced" fill="#5eead4" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Panel>
      <Panel title="Ranking" bodyClassName="p-0">
        <div className="overflow-x-auto">
          <table className="table-base">
            <thead>
              <tr>
                <th>#</th>
                <th>Supplier</th>
                <th>Now</th>
                <th>Sole-sourced</th>
                <th>Reach</th>
                <th>Sole-source parts</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.id}>
                  <td className="num text-xs text-faint">{i + 1}</td>
                  <td><EntityChip entity={{ id: r.id, name: r.name, kind: "Supplier" }} /></td>
                  <td><StatusPill status={r.status} /></td>
                  <td className="num text-sm">{r.sole_source_revenue_display}</td>
                  <td className="num text-xs text-muted">{r.revenue_reach_display}</td>
                  <td className="text-xs text-muted">{r.sole_source_parts.join(", ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

interface LeadTimeData {
  chains: { total_days: number; best_case_days: number; stages: { part: string; primary_supplier: string | null; lead_time_days: number }[] }[];
  chains_considered: number;
}

const STAGE_COLORS = ["#5eead4", "#818cf8", "#60a5fa", "#a78bfa", "#22d3ee", "#34d399", "#fbbf24", "#f472b6", "#94a3b8", "#f87171"];

function LeadTime() {
  const [product, setProduct] = useState<EntityRef | null>(null);
  const { data, error, isLoading } = useSWR<ToolResult<LeadTimeData>>(
    product ? ["critical_lead_time", { product: product.id, top_k: 5 }] : null,
    toolFetcher<LeadTimeData>,
  );
  const chains = data?.data.chains ?? [];
  const longest = Math.max(1, ...chains.map((c) => c.total_days));
  return (
    <Panel
      title="Sourcing lead time"
      subtitle="Longest raw-material-to-product chains, summing each stage’s primary-supplier lead time"
      actions={<EntityPicker className="w-72" kinds={["Product"]} value={product} onChange={setProduct} placeholder="Choose a product" />}
    >
      {!product && <Empty icon={<Clock className="size-8" />} title="Choose a product">The graph walks every BOM path down to raw materials.</Empty>}
      {error && <ErrorBox error={error} />}
      {isLoading && <Skeleton className="h-64" />}
      {data && (
        <div className="space-y-6">
          <div className="flex items-center gap-2 text-sm text-muted">
            <OutcomeBadge outcome={data.outcome} /> {data.data.chains_considered ?? 0} chains considered
          </div>
          {chains.map((c, i) => (
            <div key={i}>
              <div className="mb-2 flex items-baseline justify-between">
                <span className="text-sm font-medium">{i === 0 ? "Critical path" : `Path ${i + 1}`}</span>
                <span className="num text-sm">
                  {c.total_days} days <span className="text-xs text-faint">(best case {c.best_case_days})</span>
                </span>
              </div>
              <div className="flex h-9 overflow-hidden rounded-lg border border-line" style={{ width: `${(c.total_days / longest) * 100}%` }}>
                {c.stages.map((s, j) => (
                  <div
                    key={j}
                    className="flex min-w-0 items-center justify-center border-r border-ink-950/60 px-1 text-[11px] font-medium text-ink-950"
                    style={{ width: `${(s.lead_time_days / Math.max(c.total_days, 1)) * 100}%`, background: STAGE_COLORS[j % STAGE_COLORS.length] }}
                    title={`${s.part} · ${s.primary_supplier ?? "in-house"} · ${s.lead_time_days} days`}
                  >
                    <span className="truncate">{s.lead_time_days}d</span>
                  </div>
                ))}
              </div>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted">
                {c.stages.map((s, j) => (
                  <span key={j} className="flex items-center gap-1.5">
                    <span className="size-2 rounded-sm" style={{ background: STAGE_COLORS[j % STAGE_COLORS.length] }} />
                    {s.part} <span className="text-faint">· {s.primary_supplier ?? "in-house"}</span>
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

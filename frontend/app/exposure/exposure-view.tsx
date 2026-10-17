"use client";

import { Globe2, Layers, Radar, TriangleAlert } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { Bar as RBar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import useSWR from "swr";

import { EntityPicker } from "@/components/EntityPicker";
import { Empty, EntityChip, ErrorBox, Kpi, OutcomeBadge, PageHeader, Panel, Skeleton } from "@/components/ui";
import { api, runTool } from "@/lib/api";
import type { EntityDetail, EntityRef, ToolResult } from "@/lib/types";

interface ExposureData {
  start: EntityRef;
  products: EntityRef[];
  countries: { country: string; suppliers: number }[];
  suppliers_by_tier: Record<string, number>;
  disrupted_suppliers: ExposureSupplier[];
  suppliers: ExposureSupplier[];
}

interface ExposureSupplier {
  id: string;
  name: string;
  region: string | null;
  country: string | null;
  tier: number;
  parts: { part: string; share: number }[];
  active_disruptions: string[];
}

const tooltipStyle = {
  background: "#0a1120",
  border: "1px solid rgba(148,163,184,0.2)",
  borderRadius: 10,
  fontSize: 12,
};

export function ExposureView() {
  const params = useSearchParams();
  const router = useRouter();
  const [entity, setEntity] = useState<EntityRef | null>(null);

  useEffect(() => {
    const id = params.get("entity");
    if (!id || entity?.id === id) return;
    api<EntityDetail>(`/v1/entity?id=${encodeURIComponent(id)}`)
      .then((d) => setEntity({ id: d.id, name: d.name, kind: d.kind }))
      .catch(() => undefined);
  }, [params, entity?.id]);

  const { data, error, isLoading } = useSWR<ToolResult<ExposureData>>(
    entity ? ["upstream_exposure", entity.id] : null,
    ([, id]: [string, string]) => runTool<ExposureData>("upstream_exposure", { entity: id }),
  );
  const d = data?.data;
  const tiers = Object.entries(d?.suppliers_by_tier ?? {}).map(([tier, n]) => ({ tier: `Tier ${tier}`, suppliers: n }));
  const countries = (d?.countries ?? []).slice(0, 12);
  const topCountryShare =
    d && d.suppliers.length ? Math.round(((countries[0]?.suppliers ?? 0) / d.suppliers.length) * 100) : 0;

  return (
    <>
      <PageHeader
        title="Exposure"
        description="Walk down from a product or customer to every supplier at every tier. Hidden deep-tier dependencies show up here before they show up in a shortage."
        actions={
          <EntityPicker
            className="w-80"
            kinds={["Product", "Customer"]}
            value={entity}
            onChange={(e) => {
              setEntity(e);
              router.replace(e ? `/exposure?entity=${encodeURIComponent(e.id)}` : "/exposure");
            }}
            placeholder="Product or customer…"
          />
        }
      />

      {!entity && (
        <Panel>
          <Empty icon={<Radar className="size-10" />} title="Choose a product or customer">
            The graph collects every supplier reachable through the bill of materials and flags the ones
            under active disruption.
          </Empty>
        </Panel>
      )}
      {error && <ErrorBox error={error} />}
      {isLoading && <Skeleton className="h-96" />}

      {data && !d?.suppliers && (
        <Panel>
          <div className="flex items-center gap-2">
            <OutcomeBadge outcome={data.outcome} />
            <p className="text-sm text-muted">{data.summary}</p>
          </div>
        </Panel>
      )}

      {d?.suppliers && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Kpi label="Suppliers" value={d.suppliers.length} icon={<Layers className="size-4" />} hint={`${d.products.length} product(s)`} />
            <Kpi label="Countries" value={d.countries.length} icon={<Globe2 className="size-4" />} hint={`${countries[0]?.country ?? "—"} holds ${topCountryShare}%`} />
            <Kpi label="Deepest tier" value={Math.max(...d.suppliers.map((s) => s.tier))} />
            <Kpi label="Under disruption" value={d.disrupted_suppliers.length} tone={d.disrupted_suppliers.length ? "danger" : "ok"} icon={<TriangleAlert className="size-4" />} />
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <Panel title="Suppliers by tier" subtitle="Tier = BOM hops between the supplied part and the product">
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={tiers}>
                    <CartesianGrid vertical={false} stroke="rgba(148,163,184,0.1)" />
                    <XAxis dataKey="tier" stroke="#8b97ad" fontSize={11} />
                    <YAxis allowDecimals={false} stroke="#5b6780" fontSize={11} />
                    <Tooltip cursor={{ fill: "rgba(148,163,184,0.06)" }} contentStyle={tooltipStyle} />
                    <RBar dataKey="suppliers" radius={[6, 6, 0, 0]}>
                      {tiers.map((_, i) => (
                        <Cell key={i} fill={`rgba(94,234,212,${0.95 - i * 0.12})`} />
                      ))}
                    </RBar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Panel>
            <Panel title="Country concentration" subtitle="Suppliers per country">
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={countries} layout="vertical" margin={{ left: 4 }}>
                    <CartesianGrid horizontal={false} stroke="rgba(148,163,184,0.1)" />
                    <XAxis type="number" allowDecimals={false} stroke="#5b6780" fontSize={11} />
                    <YAxis type="category" dataKey="country" width={40} stroke="#8b97ad" fontSize={11} />
                    <Tooltip cursor={{ fill: "rgba(148,163,184,0.06)" }} contentStyle={tooltipStyle} />
                    <RBar dataKey="suppliers" fill="#818cf8" radius={[0, 6, 6, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Panel>
          </div>

          {d.disrupted_suppliers.length > 0 && (
            <Panel title="Exposed to active disruptions" className="ring-1 ring-danger/20">
              <ul className="grid gap-3 md:grid-cols-2">
                {d.disrupted_suppliers.map((s) => (
                  <li key={s.id} className="rounded-xl border border-danger/20 bg-danger/5 p-3">
                    <div className="flex items-center gap-2">
                      <EntityChip entity={{ id: s.id, name: s.name, kind: "Supplier" }} />
                      <span className="ml-auto rounded-md bg-ink-800 px-1.5 py-0.5 text-[11px] text-muted">tier {s.tier}</span>
                    </div>
                    <p className="mt-2 text-xs text-danger">{s.active_disruptions.join(" · ")}</p>
                    <p className="mt-1 text-xs text-muted">Supplies {s.parts.map((p) => p.part).join(", ")}</p>
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          <Panel title="All upstream suppliers" bodyClassName="p-0">
            <div className="overflow-x-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Tier</th>
                    <th>Supplier</th>
                    <th>Location</th>
                    <th>Supplies</th>
                  </tr>
                </thead>
                <tbody>
                  {d.suppliers.map((s) => (
                    <tr key={s.id}>
                      <td className="num text-xs text-muted">{s.tier}</td>
                      <td><EntityChip entity={{ id: s.id, name: s.name, kind: "Supplier" }} /></td>
                      <td className="text-xs text-muted">{s.region} {s.country ? `· ${s.country}` : ""}</td>
                      <td className="text-xs text-muted">
                        {s.parts.map((p) => `${p.part} (${Math.round(p.share * 100)}%)`).join(", ")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        </div>
      )}
    </>
  );
}

"use client";

import { Factory, Play, Sparkles, TriangleAlert, Truck, Users, Zap } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { EntityPicker } from "@/components/EntityPicker";
import { useAnalyst } from "@/components/providers";
import {
  Bar,
  Empty,
  EntityChip,
  ErrorBox,
  Kpi,
  OutcomeBadge,
  PageHeader,
  Panel,
  PathChain,
  Spinner,
  StatusPill,
} from "@/components/ui";
import { api, runTool } from "@/lib/api";
import { humanize, money, pct } from "@/lib/format";
import { useMeta } from "@/lib/hooks";
import type { EntityDetail, EntityKind, EntityRef, ImpactData, ToolResult } from "@/lib/types";

const START_KINDS: EntityKind[] = ["Disruption", "Region", "Port", "Supplier", "Part"];

export function SimulatorView() {
  const params = useSearchParams();
  const router = useRouter();
  const analyst = useAnalyst();
  const { data: meta } = useMeta();
  const [entity, setEntity] = useState<EntityRef | null>(null);
  const [useCustomDuration, setUseCustomDuration] = useState(false);
  const [duration, setDuration] = useState(30);
  const [depth, setDepth] = useState(8);
  const [result, setResult] = useState<ToolResult<ImpactData> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [running, setRunning] = useState(false);

  const run = async (target: EntityRef | null = entity) => {
    if (!target) return;
    setRunning(true);
    setError(null);
    try {
      const args: Record<string, unknown> = { entity: target.id, kind: target.kind, max_depth: depth };
      if (useCustomDuration || target.kind !== "Disruption") args.duration_days = duration;
      setResult(await runTool<ImpactData>("disruption_impact", args));
      router.replace(`/simulator?entity=${encodeURIComponent(target.id)}`);
    } catch (e) {
      setError(e);
    } finally {
      setRunning(false);
    }
  };

  // Deep link: /simulator?entity=ID runs immediately.
  useEffect(() => {
    const id = params.get("entity");
    if (!id || entity?.id === id) return;
    api<EntityDetail>(`/v1/entity?id=${encodeURIComponent(id)}`)
      .then((d) => {
        const ref = { id: d.id, name: d.name, kind: d.kind };
        setEntity(ref);
        void run(ref);
      })
      .catch(setError);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  const data = result?.data;
  const currency = data?.currency ?? meta?.currency ?? "USD";

  return (
    <>
      <PageHeader
        title="Impact simulator"
        description="Knock out any disruption, region, port, supplier or part and watch the outage propagate through the bill of materials to products, customers and revenue."
      />

      <div className="grid gap-6 xl:grid-cols-[340px_1fr]">
        <Panel title="Scenario" className="h-fit xl:sticky xl:top-20">
          <div className="space-y-5">
            <label className="block space-y-1.5">
              <span className="text-xs text-muted">What goes down?</span>
              <EntityPicker kinds={START_KINDS} value={entity} onChange={setEntity} placeholder="Port, region, supplier, part…" />
            </label>

            <div className="space-y-2">
              {entity?.kind === "Disruption" && (
                <label className="flex items-center gap-2 text-xs text-muted">
                  <input type="checkbox" checked={useCustomDuration} onChange={(e) => setUseCustomDuration(e.target.checked)} className="accent-teal-300" />
                  Override the disruption&apos;s expected end
                </label>
              )}
              {(entity?.kind !== "Disruption" || useCustomDuration) && (
                <label className="block space-y-1.5">
                  <span className="flex justify-between text-xs text-muted">
                    Outage length <span className="num text-fg">{duration} days</span>
                  </span>
                  <input type="range" min={1} max={180} value={duration} onChange={(e) => setDuration(Number(e.target.value))} className="w-full accent-teal-300" />
                </label>
              )}
            </div>

            <label className="block space-y-1.5">
              <span className="flex justify-between text-xs text-muted">
                BOM depth <span className="num text-fg">{depth} hops</span>
              </span>
              <input type="range" min={1} max={10} value={depth} onChange={(e) => setDepth(Number(e.target.value))} className="w-full accent-teal-300" />
            </label>

            <button className="btn btn-primary w-full" disabled={!entity || running} onClick={() => run()}>
              {running ? <Spinner /> : <Play className="size-4" />} Run simulation
            </button>
            <p className="text-[11px] leading-relaxed text-faint">
              A supplier counts as knocked out when it sits in the affected area, is itself affected, or
              loses every export port. Days of cover add up inventory held at each BOM stage.
            </p>
          </div>
        </Panel>

        <div className="min-w-0 space-y-6">
          {error ? <ErrorBox error={error} /> : null}
          {!result && !running && !error && (
            <Panel>
              <Empty icon={<Zap className="size-10" />} title="Pick something to break">
                Choose a port, region, supplier, part or an active disruption and run the scenario.
              </Empty>
            </Panel>
          )}
          {running && !result && (
            <Panel>
              <div className="grid place-items-center py-16 text-muted">
                <Spinner className="size-6" />
              </div>
            </Panel>
          )}

          {result && (
            <>
              <Panel
                title={
                  <span className="flex items-center gap-2">
                    {data?.start ? <EntityChip entity={data.start} /> : null}
                    <OutcomeBadge outcome={result.outcome} />
                  </span>
                }
                actions={
                  meta?.llm_configured && entity ? (
                    <button className="btn h-8" onClick={() => analyst.open(`Explain the impact of ${entity.name} (${entity.id}) and recommend mitigations.`)}>
                      <Sparkles className="size-3.5" /> Explain
                    </button>
                  ) : null
                }
              >
                <p className="text-sm leading-relaxed text-muted">{result.summary}</p>
                {result.candidates.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {result.candidates.map((c) => (
                      <button key={c.id} className="chip hover:bg-ink-700" onClick={() => { setEntity(c); void run(c); }}>
                        {c.name} <span className="text-faint">{c.kind}</span>
                      </button>
                    ))}
                  </div>
                )}
                <p className="mt-2 text-[11px] text-faint">{result.query_ms.toFixed(0)} ms of graph traversal</p>
              </Panel>

              {data?.products && (
                <>
                  <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
                    <Kpi label="Revenue at risk" value={data.revenue_at_risk_display} tone={data.revenue_at_risk ? "danger" : "ok"} hint={`${data.revenue_exposed_display} exposed`} />
                    <Kpi label="Suppliers knocked out" value={data.suppliers_hit.length} tone="warn" hint={data.suppliers_degraded.length ? `${data.suppliers_degraded.length} degraded` : undefined} />
                    <Kpi label="Parts losing supply" value={data.parts.length} />
                    <Kpi label="Outage" value={data.outage_days != null ? `${data.outage_days}d` : "—"} hint="vs. inventory cover below" />
                  </div>

                  <Panel title="Products" subtitle="Line-stop risk means at least one input has no unaffected supplier">
                    <div className="space-y-4">
                      {data.products.map((p) => {
                        const path = result.paths.find((x) => x[x.length - 1]?.id === p.id);
                        const cover = p.days_of_cover ?? 0;
                        const outage = data.outage_days ?? 0;
                        const horizon = Math.max(cover, outage, 1);
                        return (
                          <div key={p.id} className="rounded-xl border border-line bg-ink-950/40 p-4">
                            <div className="flex flex-wrap items-center gap-2">
                              <Factory className="size-4 text-ok" />
                              <EntityChip entity={{ id: p.id, name: p.name, kind: "Product" }} />
                              <StatusPill status={p.status} />
                              {p.stockout_expected && (
                                <span className="inline-flex items-center gap-1 text-xs text-danger">
                                  <TriangleAlert className="size-3.5" /> stock runs out
                                </span>
                              )}
                              <span className="num ml-auto text-sm">{money(p.annual_revenue, currency)}/yr</span>
                            </div>
                            {data.outage_days != null && p.days_of_cover != null && (
                              <div className="mt-3 space-y-1.5">
                                <div className="flex justify-between text-[11px] text-faint">
                                  <span>Inventory cover {cover}d</span>
                                  <span>Outage {outage}d</span>
                                </div>
                                <div className="relative h-2 overflow-hidden rounded-full bg-ink-700">
                                  <div className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-accent to-accent-2" style={{ width: `${(cover / horizon) * 100}%` }} />
                                  <div className="absolute inset-y-0 w-0.5 bg-danger" style={{ left: `${Math.min(99.5, (outage / horizon) * 100)}%` }} />
                                </div>
                              </div>
                            )}
                            {path && (
                              <div className="mt-3">
                                <p className="mb-1.5 text-[11px] text-faint">Critical path</p>
                                <PathChain path={path} />
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </Panel>

                  <div className="grid gap-6 lg:grid-cols-2">
                    <Panel title="Suppliers" subtitle="Knocked out and degraded">
                      <ul className="space-y-2">
                        {data.suppliers_hit.map((s) => (
                          <li key={s.id} className="flex items-center gap-2">
                            <Truck className="size-4 text-danger" />
                            <EntityChip entity={{ id: s.id, name: s.name, kind: "Supplier" }} />
                            <span className="ml-auto text-[11px] text-muted">{humanize(s.reason)}</span>
                          </li>
                        ))}
                        {data.suppliers_degraded.map((s) => (
                          <li key={s.id} className="flex items-center gap-2">
                            <Truck className="size-4 text-warn" />
                            <EntityChip entity={{ id: s.id, name: s.name, kind: "Supplier" }} />
                            <span className="ml-auto text-[11px] text-muted">still ships via {s.open_ports.join(", ")}</span>
                          </li>
                        ))}
                      </ul>
                    </Panel>
                    <Panel title="Customers" subtitle="Annual revenue at risk">
                      <ul className="space-y-3">
                        {data.customers.map((c) => (
                          <li key={c.id}>
                            <div className="flex items-center gap-2">
                              <Users className="size-4 text-warn" />
                              <EntityChip entity={{ id: c.id, name: c.name, kind: "Customer" }} />
                              <span className="num ml-auto text-sm">{money(c.revenue_at_risk, currency)}</span>
                            </div>
                            <div className="mt-1.5">
                              <Bar value={c.revenue_at_risk} max={data.customers[0]?.revenue_at_risk || c.revenue_exposed || 1} tone="danger" />
                            </div>
                          </li>
                        ))}
                      </ul>
                    </Panel>
                  </div>

                  <Panel title="Parts losing supply" bodyClassName="p-0">
                    <div className="overflow-x-auto">
                      <table className="table-base">
                        <thead>
                          <tr>
                            <th>Part</th>
                            <th className="w-40">Supply lost</th>
                            <th>On hand</th>
                            <th>Unaffected alternates</th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.parts.map((p) => (
                            <tr key={p.id}>
                              <td><EntityChip entity={{ id: p.id, name: p.name, kind: "Part" }} /></td>
                              <td>
                                <div className="flex items-center gap-2">
                                  <span className="num w-10 text-xs">{pct(p.share_lost)}</span>
                                  <Bar value={p.share_lost} tone={p.share_lost >= 0.999 ? "danger" : "warn"} />
                                </div>
                              </td>
                              <td className="num text-xs text-muted">{p.inventory_days}d</td>
                              <td>
                                {p.unaffected_alternates.length === 0 ? (
                                  <span className="text-xs text-danger">None qualified</span>
                                ) : (
                                  <div className="flex flex-wrap gap-1">
                                    {p.unaffected_alternates.map((a) => (
                                      <span key={a.id} className="inline-flex items-center gap-1">
                                        <EntityChip entity={{ id: a.id, name: a.name, kind: "Supplier" }} />
                                        <span className="text-[11px] text-faint">{pct(a.share)} · {a.lead_time_days}d</span>
                                      </span>
                                    ))}
                                  </div>
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </Panel>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}

"use client";

import { FileText, Network, Radar, Sparkles, X, Zap } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";

import { api } from "@/lib/api";
import { humanize, shortDate } from "@/lib/format";
import { useMeta } from "@/lib/hooks";
import type { EntityDetail, EntityRelationship } from "@/lib/types";

import { useAnalyst, useInspector } from "./providers";
import { EntityChip, ErrorBox, KindDot, Skeleton, StatusPill } from "./ui";

const REL_LABEL: Record<string, [outgoing: string, incoming: string]> = {
  SUPPLIES: ["Supplies", "Supplied by"],
  COMPONENT_OF: ["Used in", "Built from"],
  SOLD_TO: ["Sold to", "Buys"],
  LOCATED_IN: ["Located in", "Hosts"],
  SHIPS_VIA: ["Ships via", "Export port for"],
  AFFECTS: ["Affects", "Affected by"],
};

const SIMULATABLE = new Set(["Disruption", "Region", "Port", "Supplier", "Part"]);
const EXPOSURE = new Set(["Product", "Customer"]);

export function EntityDrawer() {
  const { entityId, close } = useInspector();
  const analyst = useAnalyst();
  const { data: meta } = useMeta();
  const { data, error, isLoading, mutate } = useSWR<EntityDetail>(
    entityId ? `/v1/entity?id=${encodeURIComponent(entityId)}` : null,
  );
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [close]);

  const groups = useMemo(() => {
    const out = new Map<string, EntityRelationship[]>();
    for (const r of data?.relationships ?? []) {
      const label = REL_LABEL[r.type]?.[r.outgoing ? 0 : 1] ?? humanize(r.type);
      out.set(label, [...(out.get(label) ?? []), r]);
    }
    return [...out.entries()];
  }, [data]);

  if (!entityId) return null;

  const toggleDisruption = async (active: boolean) => {
    if (!data) return;
    setBusy(true);
    try {
      await api(`/v1/disruptions/${encodeURIComponent(data.id)}`, {
        method: "PATCH",
        body: JSON.stringify({ active }),
      });
      await mutate();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[55] flex justify-end bg-ink-950/50 backdrop-blur-[2px]" onMouseDown={close}>
      <aside
        className="h-full w-full max-w-md overflow-y-auto border-l border-line bg-ink-900/95 shadow-2xl backdrop-blur-xl animate-fade-in"
        onMouseDown={(e) => e.stopPropagation()}
        aria-label="Entity inspector"
      >
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-line bg-ink-900/95 px-5 py-3 backdrop-blur">
          <span className="eyebrow">Inspector</span>
          <button className="btn btn-ghost h-8 px-2" onClick={close} aria-label="Close inspector">
            <X className="size-4" />
          </button>
        </div>

        <div className="space-y-6 p-5">
          {isLoading && (
            <div className="space-y-3">
              <Skeleton className="h-7 w-2/3" />
              <Skeleton className="h-4 w-1/3" />
              <Skeleton className="h-32 w-full" />
            </div>
          )}
          {error && <ErrorBox error={error} onRetry={() => mutate()} />}
          {data && (
            <>
              <div>
                <div className="flex items-center gap-2 text-xs text-muted">
                  <KindDot kind={data.kind} />
                  {data.kind}
                  <span className="text-faint">· {data.id}</span>
                </div>
                <h2 className="mt-1.5 text-xl font-semibold leading-tight">{data.name}</h2>
                <div className="mt-2 flex flex-wrap gap-2">
                  {data.status && <StatusPill status={data.status} />}
                  {data.kind === "Disruption" && (
                    <StatusPill status={data.properties.active ? "disrupted" : "available"} />
                  )}
                </div>
              </div>

              <div className="flex flex-wrap gap-2">
                {SIMULATABLE.has(data.kind) && (
                  <Link className="btn h-8" href={`/simulator?entity=${encodeURIComponent(data.id)}`} onClick={close}>
                    <Zap className="size-3.5" /> Simulate outage
                  </Link>
                )}
                {EXPOSURE.has(data.kind) && (
                  <Link className="btn h-8" href={`/exposure?entity=${encodeURIComponent(data.id)}`} onClick={close}>
                    <Radar className="size-3.5" /> Exposure
                  </Link>
                )}
                <Link className="btn h-8" href={`/network?focus=${encodeURIComponent(data.id)}`} onClick={close}>
                  <Network className="size-3.5" /> In network
                </Link>
                {meta?.llm_configured && (
                  <button
                    className="btn h-8"
                    onClick={() => {
                      analyst.open(`What is the supply-chain risk around ${data.name} (${data.id})?`);
                      close();
                    }}
                  >
                    <Sparkles className="size-3.5" /> Ask analyst
                  </button>
                )}
                {data.kind === "Disruption" && (
                  <button
                    className={data.properties.active ? "btn btn-danger h-8" : "btn h-8"}
                    disabled={busy}
                    onClick={() => toggleDisruption(!data.properties.active)}
                  >
                    {data.properties.active ? "Mark resolved" : "Reactivate"}
                  </button>
                )}
              </div>

              {Object.keys(data.properties).length > 0 && (
                <section>
                  <h3 className="eyebrow mb-2">Properties</h3>
                  <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line">
                    {Object.entries(data.properties).map(([k, v]) => (
                      <div key={k} className="bg-ink-900 px-3 py-2">
                        <dt className="text-[11px] text-faint">{humanize(k)}</dt>
                        <dd className="num mt-0.5 break-words text-sm">{formatValue(k, v, meta?.currency)}</dd>
                      </div>
                    ))}
                  </dl>
                </section>
              )}

              {data.documents && data.documents.length > 0 && (
                <section>
                  <h3 className="eyebrow mb-2">Source documents</h3>
                  <ul className="space-y-1.5">
                    {data.documents.map((d) => (
                      <li key={d.id} className="flex items-center gap-2 text-sm">
                        <FileText className="size-4 text-accent" />
                        <span className="truncate">{d.filename}</span>
                        <span className="ml-auto text-xs text-faint">{shortDate(d.uploaded_at)}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              {data.passages && data.passages.length > 0 && (
                <section>
                  <h3 className="eyebrow mb-2">
                    {data.kind === "Disruption" ? "Source passages" : "Mentioned in documents"}
                  </h3>
                  <ul className="space-y-2">
                    {data.passages.map((p) => (
                      <li key={p.id} className="rounded-xl border border-line bg-ink-950/40 p-3">
                        <p className="flex items-center gap-1.5 text-[11px] text-faint">
                          <FileText className="size-3.5 text-accent" /> {p.filename} · page {p.page}
                        </p>
                        <p className="mt-1.5 line-clamp-5 text-xs leading-relaxed text-fg">{p.text}</p>
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              <section>
                <h3 className="eyebrow mb-2">Relationships</h3>
                {groups.length === 0 && <p className="text-sm text-muted">No relationships recorded.</p>}
                <div className="space-y-4">
                  {groups.map(([label, rels]) => (
                    <div key={label}>
                      <p className="mb-1.5 text-xs text-muted">
                        {label} <span className="text-faint">({rels.length})</span>
                      </p>
                      <div className="flex flex-wrap gap-1.5">
                        {rels.map((r) => (
                          <span key={`${r.type}-${r.id}-${r.outgoing}`} className="inline-flex items-center gap-1">
                            <EntityChip entity={r} />
                            {relHint(r) && <span className="text-[11px] text-faint">{relHint(r)}</span>}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            </>
          )}
        </div>
      </aside>
    </div>
  );
}

function relHint(r: EntityRelationship): string | null {
  const p = r.props ?? {};
  if (r.type === "SUPPLIES" && typeof p.share === "number") {
    return `${Math.round(p.share * 100)}% · ${p.lead_time_days ?? "?"}d`;
  }
  if (r.type === "COMPONENT_OF" && typeof p.qty === "number") return `×${p.qty}`;
  return null;
}

function formatValue(key: string, value: unknown, currency?: string): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") {
    if (key.includes("revenue") || key.includes("cost")) {
      return new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: currency ?? "USD",
        notation: "compact",
        maximumFractionDigits: 2,
      }).format(value);
    }
    if (key === "risk_score") return value.toFixed(2);
    return value.toLocaleString();
  }
  return String(value);
}

"use client";

import clsx from "clsx";
import { Crosshair, Eye, Maximize2, Network } from "lucide-react";
import dynamic from "next/dynamic";
import { useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import useSWR from "swr";

import { EntityPicker } from "@/components/EntityPicker";
import { useInspector } from "@/components/providers";
import { Empty, ErrorBox, KindDot, PageHeader, Spinner } from "@/components/ui";
import { KIND_COLOR } from "@/lib/format";
import { useMeta } from "@/lib/hooks";
import type { EntityKind, GraphPayload } from "@/lib/types";

const GraphCanvas = dynamic(() => import("@/components/graph/GraphCanvas"), {
  ssr: false,
  loading: () => (
    <div className="absolute inset-0 grid place-items-center text-muted">
      <Spinner className="size-6" />
    </div>
  ),
});

export function NetworkView() {
  const params = useSearchParams();
  const router = useRouter();
  const focus = params.get("focus");
  const [depth, setDepth] = useState(2);
  const [showInactive, setShowInactive] = useState(false);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const { open } = useInspector();
  const { data: meta } = useMeta();

  const key = focus
    ? `/v1/neighborhood?id=${encodeURIComponent(focus)}&depth=${depth}`
    : `/v1/network${showInactive ? "?include_inactive=true" : ""}`;
  const { data, error, isLoading, mutate } = useSWR<GraphPayload>(key);

  const filtered = useMemo(() => {
    if (!data) return { nodes: [], links: [] };
    const nodes = data.nodes.filter((n) => !hidden.has(n.kind));
    const ids = new Set(nodes.map((n) => n.id));
    return { nodes, links: data.links.filter((l) => ids.has(l.source) && ids.has(l.target)) };
  }, [data, hidden]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const n of data?.nodes ?? []) c[n.kind] = (c[n.kind] ?? 0) + 1;
    return c;
  }, [data]);

  const statusCounts = useMemo(() => {
    const c = { disrupted: 0, degraded: 0, affected: 0 };
    for (const n of data?.nodes ?? []) if (n.status in c) c[n.status as keyof typeof c] += 1;
    return c;
  }, [data]);

  const setFocus = (id: string | null) => {
    const sp = new URLSearchParams(params);
    if (id) sp.set("focus", id);
    else sp.delete("focus");
    router.replace(`/network${sp.size ? `?${sp}` : ""}`);
  };

  const kinds = (meta?.entity_kinds ?? (Object.keys(KIND_COLOR) as EntityKind[])).filter((k) => counts[k]);

  return (
    <>
      <PageHeader
        title="Network explorer"
        description="The supply graph itself. Click a node to inspect it, double-click to focus its neighbourhood. Red rings are suppliers knocked out by active disruptions; amber are degraded."
      />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <EntityPicker
          className="w-full sm:w-80"
          value={null}
          placeholder="Focus on an entity…"
          onChange={(e) => e && setFocus(e.id)}
        />
        {focus ? (
          <>
            <div className="inline-flex items-center gap-1 rounded-lg border border-line bg-ink-900/70 p-1 text-sm">
              <Crosshair className="ml-1.5 size-4 text-accent" />
              <span className="px-1 text-muted">Depth</span>
              {[1, 2, 3].map((d) => (
                <button
                  key={d}
                  onClick={() => setDepth(d)}
                  className={clsx("rounded-md px-2.5 py-1", d === depth ? "bg-ink-700 text-fg" : "text-muted hover:text-fg")}
                >
                  {d}
                </button>
              ))}
            </div>
            <button className="btn" onClick={() => setFocus(null)}>
              <Maximize2 className="size-4" /> Whole network
            </button>
          </>
        ) : (
          <button className={clsx("btn", showInactive && "border-accent/40 text-accent")} onClick={() => setShowInactive((s) => !s)}>
            <Eye className="size-4" /> Resolved disruptions
          </button>
        )}
        <div className="ml-auto flex flex-wrap gap-1.5">
          {kinds.map((k) => (
            <button
              key={k}
              onClick={() =>
                setHidden((h) => {
                  const next = new Set(h);
                  if (next.has(k)) next.delete(k);
                  else next.add(k);
                  return next;
                })
              }
              className={clsx("chip cursor-pointer", hidden.has(k) && "opacity-40")}
            >
              <KindDot kind={k} />
              {k}
              <span className="num text-faint">{counts[k]}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="panel relative h-[calc(100vh-15rem)] min-h-[480px] overflow-hidden">
        {error && (
          <div className="absolute inset-0 grid place-items-center p-6">
            <ErrorBox error={error} onRetry={() => mutate()} />
          </div>
        )}
        {isLoading && !data && (
          <div className="absolute inset-0 grid place-items-center text-muted">
            <Spinner className="size-6" />
          </div>
        )}
        {data && data.nodes.length === 0 && (
          <div className="absolute inset-0 grid place-items-center">
            <Empty icon={<Network className="size-10" />} title="Nothing to draw">
              This workspace has no entities yet. Import data in the Data hub.
            </Empty>
          </div>
        )}
        {data && filtered.nodes.length > 0 && (
          <GraphCanvas
            nodes={filtered.nodes}
            links={filtered.links}
            focusId={focus}
            onSelect={open}
            onExpand={(id) => setFocus(id)}
          />
        )}
        {data && (
          <div className="pointer-events-none absolute bottom-3 left-3 flex flex-wrap gap-3 rounded-lg border border-line bg-ink-950/80 px-3 py-2 text-xs text-muted backdrop-blur">
            <span>
              <span className="num text-fg">{filtered.nodes.length}</span> nodes ·{" "}
              <span className="num text-fg">{filtered.links.length}</span> links
            </span>
            <span className="flex items-center gap-1.5">
              <span className="size-2 rounded-full bg-danger" /> {statusCounts.disrupted} disrupted
            </span>
            <span className="flex items-center gap-1.5">
              <span className="size-2 rounded-full bg-warn" /> {statusCounts.degraded + statusCounts.affected} degraded / affected
            </span>
            {data.truncated && <span className="text-warn">Truncated to the configured node limit</span>}
          </div>
        )}
      </div>
    </>
  );
}

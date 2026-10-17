"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, { type ForceGraphMethods, type LinkObject, type NodeObject } from "react-force-graph-2d";

import { KIND_COLOR, STATUS_COLOR } from "@/lib/format";
import type { GraphLink, GraphNode } from "@/lib/types";

type Node = NodeObject<GraphNode>;
type Link = LinkObject<GraphNode, GraphLink>;

const LINK_COLOR: Record<string, string> = {
  SUPPLIES: "rgba(96,165,250,0.35)",
  COMPONENT_OF: "rgba(167,139,250,0.35)",
  SOLD_TO: "rgba(251,191,36,0.35)",
  LOCATED_IN: "rgba(148,163,184,0.18)",
  SHIPS_VIA: "rgba(34,211,238,0.28)",
  AFFECTS: "rgba(248,113,113,0.7)",
};

function endpointId(end: Link["source"]): string {
  return typeof end === "object" && end !== null ? String((end as Node).id) : String(end);
}

export default function GraphCanvas({
  nodes,
  links,
  focusId,
  onSelect,
  onExpand,
}: {
  nodes: GraphNode[];
  links: GraphLink[];
  focusId: string | null;
  onSelect: (id: string) => void;
  onExpand: (id: string) => void;
}) {
  const fg = useRef<ForceGraphMethods<Node, Link> | undefined>(undefined);
  const wrapper = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 800, height: 600 });
  const [hover, setHover] = useState<string | null>(null);
  const lastClick = useRef<{ id: string; t: number } | null>(null);

  // Copy so the simulation can mutate positions without touching SWR's cached data.
  const data = useMemo(
    () => ({ nodes: nodes.map((n) => ({ ...n })), links: links.map((l) => ({ ...l })) }),
    [nodes, links],
  );

  const adjacency = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const l of links) {
      if (!map.has(l.source)) map.set(l.source, new Set());
      if (!map.has(l.target)) map.set(l.target, new Set());
      map.get(l.source)!.add(l.target);
      map.get(l.target)!.add(l.source);
    }
    return map;
  }, [links]);

  const active = hover ?? focusId;
  const lit = useMemo(() => {
    if (!active) return null;
    return new Set([active, ...(adjacency.get(active) ?? [])]);
  }, [active, adjacency]);

  useEffect(() => {
    if (!wrapper.current) return;
    const ro = new ResizeObserver(([entry]) => {
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    ro.observe(wrapper.current);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const graph = fg.current;
    if (!graph) return;
    graph.d3Force("charge")?.strength(-55);
    graph.d3Force("link")?.distance((l: Link) => (l.type === "LOCATED_IN" ? 60 : 38));
  }, [data]);

  useEffect(() => {
    if (!focusId || !fg.current) return;
    const timer = setTimeout(() => {
      const node = data.nodes.find((n) => n.id === focusId) as Node | undefined;
      if (node && node.x != null && node.y != null) {
        fg.current?.centerAt(node.x, node.y, 600);
        fg.current?.zoom(2.6, 600);
      }
    }, 900);
    return () => clearTimeout(timer);
  }, [focusId, data]);

  const paintNode = useCallback(
    (node: Node, ctx: CanvasRenderingContext2D, scale: number) => {
      const r = 3 + Math.sqrt(node.degree ?? 1) * 1.6;
      const x = node.x ?? 0;
      const y = node.y ?? 0;
      const dim = lit && !lit.has(String(node.id));
      const color = KIND_COLOR[node.kind] ?? "#94a3b8";
      ctx.globalAlpha = dim ? 0.15 : 1;

      const ring = STATUS_COLOR[node.status];
      if (ring && ring !== "transparent") {
        const t = (Date.now() % 2000) / 2000;
        ctx.beginPath();
        ctx.arc(x, y, r + 2 + t * 6, 0, 2 * Math.PI);
        ctx.strokeStyle = ring;
        ctx.globalAlpha = (dim ? 0.1 : 0.55) * (1 - t);
        ctx.lineWidth = 1.5 / scale;
        ctx.stroke();
        ctx.globalAlpha = dim ? 0.15 : 1;
        ctx.beginPath();
        ctx.arc(x, y, r + 1.6, 0, 2 * Math.PI);
        ctx.fillStyle = ring;
        ctx.fill();
      }

      ctx.beginPath();
      ctx.arc(x, y, r, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.shadowColor = color;
      ctx.shadowBlur = dim ? 0 : 10;
      ctx.fill();
      ctx.shadowBlur = 0;

      const showLabel = scale > 1.8 || (lit?.has(String(node.id)) ?? false) || node.id === focusId;
      if (showLabel) {
        const size = Math.max(10 / scale, 2.5);
        ctx.font = `500 ${size}px ui-sans-serif, system-ui`;
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillStyle = dim ? "rgba(230,235,245,0.2)" : "rgba(230,235,245,0.92)";
        ctx.fillText(node.name, x, y + r + 2);
      }
      ctx.globalAlpha = 1;
    },
    [lit, focusId],
  );

  return (
    <div ref={wrapper} className="absolute inset-0">
      <ForceGraph2D<GraphNode, GraphLink>
        ref={fg}
        width={size.width}
        height={size.height}
        graphData={data}
        backgroundColor="rgba(0,0,0,0)"
        nodeId="id"
        nodeLabel={(n) => `${n.name} · ${n.kind}`}
        nodeCanvasObject={paintNode}
        nodePointerAreaPaint={(node, color, ctx) => {
          ctx.fillStyle = color;
          ctx.beginPath();
          ctx.arc(node.x ?? 0, node.y ?? 0, 4 + Math.sqrt(node.degree ?? 1) * 1.6, 0, 2 * Math.PI);
          ctx.fill();
        }}
        linkColor={(l) => {
          const s = endpointId(l.source);
          const t = endpointId(l.target);
          if (lit && !(lit.has(s) && lit.has(t) && (s === active || t === active))) return "rgba(148,163,184,0.05)";
          return LINK_COLOR[l.type] ?? "rgba(148,163,184,0.2)";
        }}
        linkWidth={(l) => (l.type === "AFFECTS" ? 1.6 : 0.8)}
        linkDirectionalArrowLength={(l) => (l.type === "LOCATED_IN" ? 0 : 3)}
        linkDirectionalArrowRelPos={1}
        linkDirectionalParticles={(l) => {
          if (!active) return l.type === "AFFECTS" ? 2 : 0;
          const s = endpointId(l.source);
          const t = endpointId(l.target);
          return s === active || t === active ? 3 : 0;
        }}
        linkDirectionalParticleWidth={2}
        linkDirectionalParticleSpeed={0.006}
        cooldownTicks={140}
        autoPauseRedraw={false}
        onEngineStop={() => {
          if (!focusId) fg.current?.zoomToFit(500, 60);
        }}
        onNodeHover={(n) => setHover(n ? String(n.id) : null)}
        onNodeClick={(n) => {
          const id = String(n.id);
          const now = Date.now();
          if (lastClick.current && lastClick.current.id === id && now - lastClick.current.t < 350) {
            onExpand(id);
          } else {
            onSelect(id);
          }
          lastClick.current = { id, t: now };
        }}
      />
    </div>
  );
}

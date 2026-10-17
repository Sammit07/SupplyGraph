"use client";

import { FileText, Search } from "lucide-react";
import { useState } from "react";

import { EntityPicker } from "@/components/EntityPicker";
import { Empty, EntityChip, ErrorBox, OutcomeBadge, Panel, Spinner } from "@/components/ui";
import { runTool } from "@/lib/api";
import { useMeta } from "@/lib/hooks";
import type { EntityRef, Passage, ToolResult } from "@/lib/types";

/** Semantic passage search: FalkorDB vector index, then graph hops to documents,
 * disruptions and the entities each passage mentions. */
export function DocumentSearch() {
  const { data: meta } = useMeta();
  const [query, setQuery] = useState("");
  const [entity, setEntity] = useState<EntityRef | null>(null);
  const [result, setResult] = useState<ToolResult<{ passages: Passage[] }> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const ready = meta?.llm_configured ?? false;

  const search = async (e: React.FormEvent) => {
    e.preventDefault();
    if (query.trim().length < 2) return;
    setBusy(true);
    setError(null);
    try {
      setResult(
        await runTool<{ passages: Passage[] }>("search_documents", {
          query: query.trim(),
          limit: 8,
          ...(entity ? { entity: entity.id } : {}),
        }),
      );
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      title="Search document passages"
      subtitle="Meaning-based search over every ingested document, connected back to the graph"
    >
      <form onSubmit={search} className="flex flex-wrap gap-2">
        <div className="relative min-w-60 flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
          <input
            className="input pl-9"
            placeholder={ready ? "e.g. when do operations resume?" : "Needs OPENAI_API_KEY on the backend"}
            value={query}
            disabled={!ready}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <EntityPicker
          className="w-full sm:w-72"
          kinds={["Supplier", "Port", "Region", "Disruption"]}
          value={entity}
          onChange={setEntity}
          placeholder="Only about… (optional)"
        />
        <button className="btn btn-primary" disabled={!ready || busy || query.trim().length < 2}>
          {busy ? <Spinner /> : <Search className="size-4" />} Search
        </button>
      </form>

      {error ? <div className="mt-4"><ErrorBox error={error} /></div> : null}

      {result && (
        <div className="mt-5 space-y-3">
          <div className="flex items-center gap-2 text-xs text-muted">
            <OutcomeBadge outcome={result.outcome} />
            {result.outcome !== "answered" && <span>{result.summary}</span>}
          </div>
          {result.outcome === "answered" && result.data.passages.length === 0 && (
            <Empty title="No relevant passages" />
          )}
          {result.data.passages?.map((p) => (
            <article key={p.id} className="rounded-xl border border-line bg-ink-950/40 p-4">
              <header className="flex flex-wrap items-center gap-2 text-xs text-muted">
                <FileText className="size-4 text-accent" />
                <span className="text-fg">{p.document.filename}</span>
                <span>page {p.page}</span>
                {p.document.issuer && <span className="text-faint">· {p.document.issuer}</span>}
                <span
                  className="num ml-auto rounded-md bg-ink-800 px-1.5 py-0.5 text-[11px]"
                  title={`Meaning ${Math.round(p.similarity * 100)}% · keywords ${Math.round(p.keyword_match * 100)}%`}
                >
                  {Math.round(p.score * 100)}% relevance
                </span>
              </header>
              <p className="mt-2 text-sm leading-relaxed text-fg">{p.text}</p>
              {(p.disruptions.length > 0 || p.mentions.length > 0) && (
                <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-line pt-3">
                  {p.disruptions.map((d) => (
                    <EntityChip key={d.id} entity={d} />
                  ))}
                  {p.mentions.map((m) => (
                    <EntityChip key={m.id} entity={m} />
                  ))}
                </div>
              )}
            </article>
          ))}
        </div>
      )}
    </Panel>
  );
}

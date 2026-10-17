"use client";

import { BookMarked, Search, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import useSWR from "swr";

import { Empty, EntityChip, ErrorBox, PageHeader, Panel, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import type { Finding } from "@/lib/types";

export function FindingsView() {
  const { data, error, isLoading, mutate } = useSWR<Finding[]>("/v1/findings");
  const [query, setQuery] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return data ?? [];
    return (data ?? []).filter(
      (f) => f.text.toLowerCase().includes(q) || f.entity.name.toLowerCase().includes(q),
    );
  }, [data, query]);

  const remove = async (id: string) => {
    setDeleting(id);
    try {
      await api(`/v1/findings/${encodeURIComponent(id)}`, { method: "DELETE" });
      await mutate();
    } finally {
      setDeleting(null);
    }
  };

  return (
    <>
      <PageHeader
        title="Findings"
        description="Durable conclusions the analyst stored on the graph, attached to the entity they are about. The analyst recalls them in later sessions, with their date."
        actions={
          <div className="relative w-72">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
            <input className="input pl-9" placeholder="Filter findings…" value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
        }
      />
      {error && <ErrorBox error={error} onRetry={() => mutate()} />}
      {isLoading && <Skeleton className="h-64" />}
      {data && rows.length === 0 && (
        <Panel>
          <Empty icon={<BookMarked className="size-10" />} title={data.length ? "No findings match" : "No findings yet"}>
            Ask the analyst to remember a conclusion, e.g. “Remember that Ningbo Magnetics is our only magnet source.”
          </Empty>
        </Panel>
      )}
      <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
        {rows.map((f) => (
          <div key={f.id} className="panel panel-hover flex flex-col p-4">
            <div className="flex items-center gap-2">
              <EntityChip entity={f.entity} />
              <span className="ml-auto text-[11px] text-faint">{shortDate(f.created_at)}</span>
            </div>
            <p className="mt-3 flex-1 text-sm leading-relaxed">{f.text}</p>
            <div className="mt-3 flex items-center justify-between text-[11px] text-faint">
              <span>
                {f.author} · session {f.session_id.slice(0, 8)}
              </span>
              <button
                className="flex items-center gap-1 hover:text-danger disabled:opacity-50"
                disabled={deleting === f.id}
                onClick={() => remove(f.id)}
              >
                <Trash2 className="size-3.5" /> Delete
              </button>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

"use client";

import { Database, Trash2 } from "lucide-react";
import { useState } from "react";

import { ErrorBox, KindDot, Panel, Spinner } from "@/components/ui";
import { api, setWorkspace } from "@/lib/api";
import { useMeta, useOverview, useWorkspaces } from "@/lib/hooks";

export function WorkspaceAdmin() {
  const { data: meta } = useMeta();
  const { data: overview } = useOverview();
  const { data: list } = useWorkspaces();
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const current = meta?.workspace ?? "";

  const remove = async () => {
    setBusy(true);
    setError(null);
    try {
      await api(`/v1/workspaces/${encodeURIComponent(current)}`, {
        method: "DELETE",
        headers: { "x-confirm": confirm },
      });
      setConfirm("");
      await setWorkspace(list?.default ?? current);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Panel title="Current workspace" subtitle="Each workspace is an isolated FalkorDB graph">
        <div className="flex items-center gap-3">
          <Database className="size-5 text-accent" />
          <div>
            <p className="font-medium">{current}</p>
            <p className="font-mono text-xs text-faint">{overview?.stats.graph}</p>
          </div>
          <span className="ml-auto text-xs text-muted">
            Currency <span className="num text-fg">{meta?.currency}</span>
          </span>
        </div>
        {overview && (
          <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3">
            {Object.entries(overview.stats.nodes).map(([k, n]) => (
              <div key={k} className="flex items-center gap-2 rounded-lg bg-ink-950/50 px-2.5 py-1.5 text-xs">
                <KindDot kind={k} />
                <span className="text-muted">{k}</span>
                <span className="num ml-auto">{n}</span>
              </div>
            ))}
          </div>
        )}
        <div className="mt-4 text-xs text-muted">
          Other workspaces: {(list?.workspaces ?? []).filter((w) => w !== current).join(", ") || "none"}. Switch or create one from the top bar.
        </div>
      </Panel>

      <Panel title="Delete workspace" subtitle="Permanently removes the graph, its findings and documents" className="ring-1 ring-danger/15">
        <label className="block space-y-1.5">
          <span className="text-xs text-muted">
            Type <code className="text-danger">{current}</code> to confirm
          </span>
          <input className="input" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </label>
        <button className="btn btn-danger mt-4" disabled={confirm !== current || busy} onClick={remove}>
          {busy ? <Spinner /> : <Trash2 className="size-4" />} Delete “{current}”
        </button>
        {error ? <div className="mt-4"><ErrorBox error={error} /></div> : null}
      </Panel>
    </div>
  );
}

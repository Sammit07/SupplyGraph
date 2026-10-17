"use client";

import clsx from "clsx";
import { Check, ChevronsUpDown, Database, Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { WORKSPACE_RE, setWorkspace } from "@/lib/api";
import { useMeta, useWorkspaces } from "@/lib/hooks";

export function WorkspaceSwitcher() {
  const { data: meta } = useMeta();
  const { data } = useWorkspaces();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const current = meta?.workspace;
  const names = Array.from(new Set([...(data?.workspaces ?? []), ...(current ? [current] : [])])).sort();
  const draftValid = WORKSPACE_RE.test(draft);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const pick = async (name: string) => {
    setOpen(false);
    setDraft("");
    await setWorkspace(name);
  };

  return (
    <div ref={ref} className="relative">
      <button className="btn h-9 gap-2" onClick={() => setOpen((o) => !o)} aria-haspopup="listbox">
        <Database className="size-4 text-accent" />
        <span className="max-w-32 truncate">{current ?? "…"}</span>
        <ChevronsUpDown className="size-3.5 text-faint" />
      </button>
      {open && (
        <div className="panel absolute right-0 z-50 mt-1 w-64 p-1.5">
          <p className="eyebrow px-2 py-1">Workspaces</p>
          <ul role="listbox">
            {names.map((name) => (
              <li key={name}>
                <button
                  className={clsx(
                    "flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-ink-800",
                    name === current && "text-accent",
                  )}
                  onClick={() => pick(name)}
                >
                  <span className="truncate">{name}</span>
                  {name === current && <Check className="ml-auto size-4" />}
                </button>
              </li>
            ))}
          </ul>
          <form
            className="mt-1 flex gap-1 border-t border-line pt-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (draftValid) pick(draft);
            }}
          >
            <input
              className="input h-8 text-xs"
              placeholder="new-workspace"
              value={draft}
              onChange={(e) => setDraft(e.target.value.toLowerCase())}
              aria-label="New workspace name"
            />
            <button className="btn h-8 px-2" disabled={!draftValid} aria-label="Create workspace">
              <Plus className="size-4" />
            </button>
          </form>
          <p className="px-2 pt-1.5 text-[11px] text-faint">
            Each workspace is its own FalkorDB graph. Import data into a new one from the Data hub.
          </p>
        </div>
      )}
    </div>
  );
}

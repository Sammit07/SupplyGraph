"use client";

import clsx from "clsx";
import { Search, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { EntityKind, EntityRef } from "@/lib/types";

import { KindDot, Spinner } from "./ui";

/** Autocomplete over the graph's full-text entity index. */
export function EntityPicker({
  kinds,
  value,
  onChange,
  placeholder = "Search the graph…",
  autoFocus,
  className,
  inputRef,
  hint,
}: {
  kinds?: EntityKind[];
  value: EntityRef | null;
  onChange: (entity: EntityRef | null) => void;
  placeholder?: string;
  autoFocus?: boolean;
  className?: string;
  /** Lets a parent focus the input (e.g. a keyboard shortcut). */
  inputRef?: React.Ref<HTMLInputElement>;
  /** Shown at the right edge of the empty input, e.g. a shortcut like "Ctrl K". */
  hint?: string;
}) {
  const [query, setQuery] = useState("");
  // Results are tagged with the query that produced them. "Loading" is derived from that
  // tag, so no sequence of typing, clearing, choosing or closing can leave a spinner on.
  const [found, setFound] = useState<{ q: string; rows: EntityRef[]; failed?: boolean } | null>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();
  const box = useRef<HTMLDivElement>(null);
  const kindKey = (kinds ?? []).join(",");
  const q = query.trim();
  const searchable = q.length >= 2;
  const current = searchable && found?.q === q ? found : null;
  const loading = searchable && !current;
  const visible = current?.rows ?? [];

  useEffect(() => {
    if (q.length < 2) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const params = new URLSearchParams({ q });
        for (const k of kindKey ? kindKey.split(",") : []) params.append("kind", k);
        const rows = await api<EntityRef[]>(`/v1/search?${params}`, { signal: controller.signal });
        setFound({ q, rows });
        setActive(0);
      } catch {
        if (!controller.signal.aborted) setFound({ q, rows: [], failed: true });
      }
    }, 180);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [q, kindKey]);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const choose = (entity: EntityRef) => {
    onChange(entity);
    setQuery("");
    setOpen(false);
  };

  if (value) {
    return (
      <div className={clsx("input flex items-center gap-2", className)}>
        <KindDot kind={value.kind} />
        <span className="truncate">{value.name}</span>
        <span className="truncate text-xs text-faint">{value.kind}</span>
        <button
          type="button"
          className="ml-auto text-faint hover:text-fg"
          onClick={() => onChange(null)}
          aria-label="Clear selection"
        >
          <X className="size-4" />
        </button>
      </div>
    );
  }

  return (
    <div ref={box} className={clsx("relative", className)}>
      <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
      <input
        ref={inputRef}
        className={clsx("input pl-9", hint && "pr-16")}
        role="combobox"
        aria-expanded={open && visible.length > 0}
        aria-controls={listId}
        placeholder={placeholder}
        value={query}
        autoFocus={autoFocus}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((a) => Math.min(a + 1, visible.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
          } else if (e.key === "Enter" && visible[active]) {
            e.preventDefault();
            choose(visible[active]);
          } else if (e.key === "Escape") {
            setOpen(false);
            e.currentTarget.blur();
          }
        }}
      />
      {loading && <Spinner className="absolute right-3 top-1/2 size-4 -translate-y-1/2 text-faint" />}
      {!loading && hint && !query && (
        <kbd className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 rounded border border-line px-1.5 text-[10px] text-faint">
          {hint}
        </kbd>
      )}
      {open && visible.length > 0 && (
        <ul
          id={listId}
          role="listbox"
          className="absolute z-50 mt-1 max-h-80 w-full min-w-72 overflow-auto rounded-xl border border-line-strong bg-ink-900 p-1 shadow-2xl shadow-black/60"
        >
          {visible.map((r, i) => (
            <li
              key={r.id}
              role="option"
              aria-selected={i === active}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(r);
              }}
              className={clsx(
                "flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-sm",
                i === active ? "bg-ink-700" : "hover:bg-ink-800",
              )}
            >
              <KindDot kind={r.kind} />
              <span className="truncate">{r.name}</span>
              <span className="ml-auto shrink-0 text-xs text-faint">
                {r.kind} · {r.id}
              </span>
            </li>
          ))}
        </ul>
      )}
      {open && current && visible.length === 0 && (
        <div className="absolute z-50 mt-1 w-full min-w-72 rounded-xl border border-line-strong bg-ink-900 px-3 py-2.5 text-sm text-muted shadow-2xl shadow-black/60">
          {current.failed ? "Search failed. Check the connection and try again." : `No matches for "${q}".`}
        </div>
      )}
    </div>
  );
}

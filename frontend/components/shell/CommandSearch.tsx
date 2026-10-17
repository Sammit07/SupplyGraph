"use client";

import { useEffect, useRef } from "react";

import { EntityPicker } from "../EntityPicker";
import { useInspector } from "../providers";

/** Header search. It stays in place: results drop down under it, and Ctrl/Cmd+K only moves
 * the cursor into it (no pop-up dialog elsewhere on the screen). */
export function CommandSearch() {
  const input = useRef<HTMLInputElement>(null);
  const { open: inspect } = useInspector();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        input.current?.focus();
        input.current?.select();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <EntityPicker
      inputRef={input}
      className="w-full max-w-md"
      value={null}
      hint="Ctrl K"
      placeholder="Search suppliers, parts, ports…"
      onChange={(entity) => {
        if (entity) inspect(entity.id);
        input.current?.blur();
      }}
    />
  );
}

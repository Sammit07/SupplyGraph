"use client";

import clsx from "clsx";
import { UploadCloud } from "lucide-react";
import { useRef, useState, type ReactNode } from "react";

export function FileDrop({
  accept,
  multiple,
  onFiles,
  title,
  hint,
  disabled,
}: {
  accept: string;
  multiple?: boolean;
  onFiles: (files: File[]) => void;
  title: ReactNode;
  hint?: ReactNode;
  disabled?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const extensions = accept.split(",").map((s) => s.trim().toLowerCase());

  const take = (list: FileList | null) => {
    if (!list) return;
    const files = Array.from(list).filter((f) => extensions.some((ext) => f.name.toLowerCase().endsWith(ext)));
    if (files.length) onFiles(multiple ? files : files.slice(0, 1));
  };

  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => input.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        take(e.dataTransfer.files);
      }}
      className={clsx(
        "flex w-full flex-col items-center justify-center gap-2 rounded-xl border border-dashed px-6 py-8 text-center transition disabled:cursor-not-allowed disabled:opacity-50",
        over ? "border-accent bg-accent/5" : "border-line-strong bg-ink-950/40 hover:border-accent/50",
      )}
    >
      <UploadCloud className={clsx("size-7", over ? "text-accent" : "text-faint")} />
      <span className="text-sm font-medium">{title}</span>
      {hint && <span className="text-xs text-muted">{hint}</span>}
      <input
        ref={input}
        type="file"
        hidden
        accept={accept}
        multiple={multiple}
        onChange={(e) => {
          take(e.target.files);
          e.target.value = "";
        }}
      />
    </button>
  );
}

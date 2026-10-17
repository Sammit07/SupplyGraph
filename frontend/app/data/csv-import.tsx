"use client";

import clsx from "clsx";
import { CheckCircle2, CircleAlert, FileSpreadsheet, FlaskConical, Trash2, Upload, X } from "lucide-react";
import { useState } from "react";
import { mutate } from "swr";

import { FileDrop } from "@/components/FileDrop";
import { ErrorBox, Panel, Spinner } from "@/components/ui";
import { apiReport } from "@/lib/api";
import { bytes } from "@/lib/format";
import { useMeta } from "@/lib/hooks";
import type { ImportReport } from "@/lib/types";

export function CsvImport() {
  const { data: meta } = useMeta();
  const [files, setFiles] = useState<File[]>([]);
  const [mode, setMode] = useState<"merge" | "replace">("merge");
  const [busy, setBusy] = useState<"validate" | "import" | "sample" | null>(null);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [error, setError] = useState<unknown>(null);
  const limit = meta?.limits.import_max_bytes ?? 4_000_000;
  const total = files.reduce((n, f) => n + f.size, 0);

  const submit = async (payload: File[], dryRun: boolean, kind: "validate" | "import" | "sample") => {
    setBusy(kind);
    setError(null);
    try {
      const form = new FormData();
      for (const f of payload) form.append("files", f, f.name);
      const result = await apiReport<ImportReport>(`/v1/imports?mode=${mode}&dry_run=${dryRun}`, {
        method: "POST",
        body: form,
      });
      setReport(result);
      if (result.written) await mutate(() => true);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };

  const loadSample = async () => {
    if (!meta) return;
    setBusy("sample");
    try {
      // Sample files are named after the backend's dataset names.
      const fetched = await Promise.all(
        meta.datasets.map(async (d) => {
          const res = await fetch(`/samples/csv/${d.name}.csv`);
          if (!res.ok) return null;
          return new File([await res.blob()], `${d.name}.csv`, { type: "text/csv" });
        }),
      );
      const sample = fetched.filter((f): f is File => f !== null);
      setFiles(sample);
      await submit(sample, false, "sample");
    } catch (e) {
      setError(e);
      setBusy(null);
    }
  };

  const errors = report?.issues.filter((i) => i.severity === "error") ?? [];
  const warnings = report?.issues.filter((i) => i.severity === "warning") ?? [];

  return (
    <div className="grid gap-6 xl:grid-cols-[1fr_420px]">
      <Panel title="Import network data" subtitle={`Into workspace “${meta?.workspace ?? "…"}”. Files are matched to datasets by their header row.`}>
        <div className="space-y-5">
          <FileDrop
            accept=".csv,.tsv,.txt"
            multiple
            onFiles={(f) => {
              setFiles((prev) => {
                const byName = new Map(prev.map((p) => [p.name, p]));
                for (const file of f) byName.set(file.name, file);
                return [...byName.values()];
              });
              setReport(null);
            }}
            title="Drop CSV extracts here, or click to choose"
            hint="Item master, BOM, approved vendor list, suppliers, locations, shipping lanes, customers, sales, risk events"
          />

          {files.length > 0 && (
            <ul className="divide-y divide-line/60 rounded-xl border border-line">
              {files.map((f) => (
                <li key={f.name} className="flex items-center gap-3 px-3 py-2 text-sm">
                  <FileSpreadsheet className="size-4 text-accent" />
                  <span className="truncate">{f.name}</span>
                  {report?.files[f.name] && <span className="chip text-[11px]">{report.files[f.name]}</span>}
                  <span className="num ml-auto text-xs text-faint">{bytes(f.size)}</span>
                  <button className="text-faint hover:text-danger" onClick={() => setFiles(files.filter((x) => x !== f))} aria-label={`Remove ${f.name}`}>
                    <X className="size-4" />
                  </button>
                </li>
              ))}
              <li className="flex items-center justify-between px-3 py-2 text-xs text-muted">
                <span className={clsx(total > limit && "text-danger")}>
                  {bytes(total)} of {bytes(limit)} upload limit
                </span>
                <button className="flex items-center gap-1 hover:text-fg" onClick={() => { setFiles([]); setReport(null); }}>
                  <Trash2 className="size-3.5" /> Clear
                </button>
              </li>
            </ul>
          )}

          <fieldset className="grid gap-2 sm:grid-cols-2">
            {(
              [
                ["merge", "Merge", "Upsert by id. Keeps findings, documents and PDF-derived disruptions."],
                ["replace", "Replace", "Build a fresh graph and swap it in. Everything else is discarded."],
              ] as const
            ).map(([value, label, help]) => (
              <label
                key={value}
                className={clsx(
                  "cursor-pointer rounded-xl border p-3 transition",
                  mode === value ? "border-accent/50 bg-accent/5" : "border-line hover:border-line-strong",
                )}
              >
                <span className="flex items-center gap-2 text-sm font-medium">
                  <input type="radio" name="mode" value={value} checked={mode === value} onChange={() => setMode(value)} className="accent-teal-300" />
                  {label}
                </span>
                <span className="mt-1 block text-xs text-muted">{help}</span>
              </label>
            ))}
          </fieldset>

          <div className="flex flex-wrap gap-2">
            <button className="btn" disabled={!files.length || busy !== null || total > limit} onClick={() => submit(files, true, "validate")}>
              {busy === "validate" ? <Spinner /> : <CheckCircle2 className="size-4" />} Validate
            </button>
            <button className="btn btn-primary" disabled={!files.length || busy !== null || total > limit} onClick={() => submit(files, false, "import")}>
              {busy === "import" ? <Spinner /> : <Upload className="size-4" />} Import
            </button>
            <button className="btn ml-auto" disabled={busy !== null || !meta} onClick={loadSample}>
              {busy === "sample" ? <Spinner /> : <FlaskConical className="size-4" />} Load sample dataset
            </button>
          </div>
          {error ? <ErrorBox error={error} /> : null}
        </div>
      </Panel>

      <Panel title="Result">
        {!report && <p className="text-sm text-muted">Validate first to see how each file maps to a dataset and any row-level issues.</p>}
        {report && (
          <div className="space-y-4">
            <div
              className={clsx(
                "flex items-center gap-2 rounded-xl border px-3 py-2.5 text-sm",
                report.ok ? "border-ok/30 bg-ok/5 text-ok" : "border-danger/30 bg-danger/5 text-danger",
              )}
            >
              {report.ok ? <CheckCircle2 className="size-4" /> : <CircleAlert className="size-4" />}
              {report.ok
                ? report.written
                  ? `Imported into “${report.workspace}” (${report.mode})`
                  : "Valid. Nothing was written (dry run)."
                : `${errors.length} error(s): nothing was written`}
            </div>

            <div>
              <p className="eyebrow mb-2">Rows parsed</p>
              <div className="grid grid-cols-2 gap-1.5">
                {Object.entries(report.rows).map(([name, n]) => (
                  <div key={name} className="flex justify-between rounded-lg bg-ink-950/50 px-2.5 py-1.5 text-xs">
                    <span className="text-muted">{name}</span>
                    <span className="num">{n}</span>
                  </div>
                ))}
              </div>
            </div>

            {report.written && (
              <div>
                <p className="eyebrow mb-2">Written to the graph</p>
                <div className="grid grid-cols-2 gap-1.5">
                  {Object.entries(report.written).filter(([, n]) => n > 0).map(([name, n]) => (
                    <div key={name} className="flex justify-between rounded-lg bg-ink-950/50 px-2.5 py-1.5 text-xs">
                      <span className="text-muted">{name}</span>
                      <span className="num">{n}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {(errors.length > 0 || warnings.length > 0) && (
              <div>
                <p className="eyebrow mb-2">Issues</p>
                <ul className="max-h-80 space-y-1.5 overflow-auto">
                  {[...errors, ...warnings].map((i, idx) => (
                    <li key={idx} className="rounded-lg bg-ink-950/50 px-2.5 py-1.5 text-xs">
                      <span className={i.severity === "error" ? "text-danger" : "text-warn"}>{i.severity}</span>{" "}
                      {i.file && (
                        <span className="font-mono text-faint">
                          {i.file}
                          {i.row ? `:${i.row}` : ""}
                          {i.column ? ` [${i.column}]` : ""}
                        </span>
                      )}{" "}
                      <span className="text-fg">{i.message}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </Panel>
    </div>
  );
}

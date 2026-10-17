"use client";

import { Download } from "lucide-react";

import { ErrorBox, Panel, Skeleton } from "@/components/ui";
import { API_BASE } from "@/lib/api";
import { useMeta } from "@/lib/hooks";

export function Templates() {
  const { data: meta, error } = useMeta();
  if (error) return <ErrorBox error={error} />;
  if (!meta) return <Skeleton className="h-96" />;
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      {meta.datasets.map((d) => (
        <Panel
          key={d.name}
          title={
            <span className="flex items-center gap-2">
              {d.title} <code className="text-xs font-normal text-faint">{d.name}.csv</code>
              {d.required ? (
                <span className="rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">required</span>
              ) : (
                <span className="rounded bg-ink-800 px-1.5 py-0.5 text-[10px] text-muted">optional</span>
              )}
            </span>
          }
          subtitle={d.description}
          actions={
            <a className="btn h-8" href={`${API_BASE}/v1/datasets/${d.name}/template`} download>
              <Download className="size-3.5" /> Template
            </a>
          }
          bodyClassName="p-0"
        >
          <table className="table-base">
            <thead>
              <tr>
                <th>Column</th>
                <th>Type</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              {d.columns.map((c) => (
                <tr key={c.name}>
                  <td className="font-mono text-xs">
                    {c.name}
                    {c.required && <span className="text-danger">*</span>}
                  </td>
                  <td className="text-xs text-muted">
                    {c.type}
                    {c.choices.length > 0 && <div className="text-faint">{c.choices.join(" | ")}</div>}
                  </td>
                  <td className="text-xs text-muted">
                    {c.description}
                    {c.example && <div className="font-mono text-faint">e.g. {c.example}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      ))}
    </div>
  );
}

"use client";

import { FileText } from "lucide-react";
import useSWR from "swr";

import { useInspector } from "@/components/providers";
import { Empty, ErrorBox, KindDot, Panel, Skeleton } from "@/components/ui";
import { shortDate } from "@/lib/format";
import type { IngestedDocument } from "@/lib/types";

export function DocumentsList() {
  const { data, error, isLoading } = useSWR<IngestedDocument[]>("/v1/documents");
  const { open } = useInspector();
  return (
    <Panel title="Ingested documents" subtitle="Provenance: each disruption links back to the document that reported it" bodyClassName="p-0">
      {error && <div className="p-5"><ErrorBox error={error} /></div>}
      {isLoading && <div className="p-5"><Skeleton className="h-24" /></div>}
      {data && data.length === 0 && <Empty icon={<FileText className="size-8" />} title="No documents yet" />}
      {data && data.length > 0 && (
        <div className="overflow-x-auto">
          <table className="table-base">
            <thead>
              <tr>
                <th>Document</th>
                <th>Issuer</th>
                <th>Added</th>
                <th>Passages</th>
                <th>Disruptions</th>
              </tr>
            </thead>
            <tbody>
              {data.map((d) => (
                <tr key={d.id}>
                  <td className="flex items-center gap-2 text-sm">
                    <FileText className="size-4 text-accent" /> {d.filename}
                  </td>
                  <td className="text-xs text-muted">{d.issuer ?? "—"}</td>
                  <td className="text-xs text-muted">{shortDate(d.uploaded_at)}</td>
                  <td className="num text-xs text-muted">{d.passages || "—"}</td>
                  <td>
                    <div className="flex flex-wrap gap-1">
                      {d.disruptions.map((x) => (
                        <button key={x.id} className="chip hover:bg-ink-700" onClick={() => open(x.id)}>
                          <KindDot kind="Disruption" /> {x.name}
                          {!x.active && <span className="text-faint">· resolved</span>}
                        </button>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

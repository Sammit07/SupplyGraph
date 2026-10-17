"use client";

import clsx from "clsx";
import { CheckCircle2, FileText, Quote, ScanText, Send, TriangleAlert } from "lucide-react";
import { useState } from "react";
import useSWR, { mutate } from "swr";

import { EntityPicker } from "@/components/EntityPicker";
import { FileDrop } from "@/components/FileDrop";
import { useInspector } from "@/components/providers";
import { ErrorBox, KindDot, Panel, Spinner, StatusPill } from "@/components/ui";
import { api } from "@/lib/api";
import { bytes } from "@/lib/format";
import { useMeta } from "@/lib/hooks";
import type { CommitResult, EntityRef, Proposal, ProposedEvent, SampleDocument } from "@/lib/types";

interface DraftTarget {
  name: string;
  evidence: string;
  status: string;
  candidates: EntityRef[];
  chosen: EntityRef | null;
}

interface DraftEvent extends Omit<ProposedEvent, "targets"> {
  include: boolean;
  targets: DraftTarget[];
}

const CATEGORIES = ["weather", "geopolitical", "labor", "operational", "logistics", "quality", "cyber", "other"];

function toDraft(p: Proposal): DraftEvent[] {
  return p.events.map((e) => ({
    ...e,
    include: true,
    targets: e.targets.map((t) => ({
      name: t.name,
      evidence: t.evidence,
      status: t.status,
      candidates: t.candidates,
      chosen: t.match,
    })),
  }));
}

export function PdfIntake() {
  const { data: meta } = useMeta();
  const { open } = useInspector();
  const { data: samples } = useSWR<SampleDocument[]>("/samples/pdf/index.json", (url: string) =>
    fetch(url).then((r) => (r.ok ? r.json() : [])),
  );
  const [file, setFile] = useState<File | null>(null);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [drafts, setDrafts] = useState<DraftEvent[]>([]);
  const [busy, setBusy] = useState<"extract" | "commit" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [committed, setCommitted] = useState<CommitResult | null>(null);
  const llmReady = meta?.llm_configured ?? false;

  const extract = async (f: File) => {
    setFile(f);
    setBusy("extract");
    setError(null);
    setProposal(null);
    setCommitted(null);
    try {
      const form = new FormData();
      form.append("file", f, f.name);
      const p = await api<Proposal>("/v1/documents/extract", { method: "POST", body: form });
      setProposal(p);
      setDrafts(toDraft(p));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };

  const trySample = async (s: SampleDocument) => {
    const res = await fetch(`/samples/pdf/${s.file}`);
    if (!res.ok) return setError(new Error(`Could not load ${s.file}`));
    await extract(new File([await res.blob()], s.file, { type: "application/pdf" }));
  };

  const patch = (i: number, fn: (d: DraftEvent) => DraftEvent) =>
    setDrafts((all) => all.map((d, j) => (j === i ? fn(d) : d)));

  const ready = drafts.filter((d) => d.include);
  const problems = ready.flatMap((d) => [
    ...(d.start_date ? [] : [`“${d.title}” needs a start date`]),
    ...(d.targets.some((t) => t.chosen) ? [] : [`“${d.title}” needs at least one confirmed entity`]),
  ]);

  const commit = async () => {
    if (!proposal || !file) return;
    setBusy("commit");
    setError(null);
    try {
      const payload = {
        document: proposal.document,
        events: ready.map((d) => ({
          id: d.proposed_id,
          title: d.title,
          category: d.category,
          severity: d.severity,
          start_date: d.start_date,
          expected_end_date: d.expected_end_date || null,
          summary: d.summary,
          affected_ids: Array.from(new Set(d.targets.flatMap((t) => (t.chosen ? [t.chosen.id] : [])))),
          // how the document wrote each name, so passages can be linked to the entity
          mentions: Object.fromEntries(d.targets.flatMap((t) => (t.chosen ? [[t.chosen.id, t.name]] : []))),
        })),
      };
      // The PDF is sent again so its passages can be indexed; the server checks its
      // fingerprint against the reviewed proposal.
      const form = new FormData();
      form.append("payload", JSON.stringify(payload));
      form.append("file", file, file.name);
      const result = await api<CommitResult>("/v1/documents/commit", { method: "POST", body: form });
      setCommitted(result);
      await mutate(() => true);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Panel
      title="Risk document intake"
      subtitle="The model reads the PDF and extracts each risk event; FalkorDB’s full-text index matches every named place and supplier to the graph. You confirm before anything is written."
    >
      {!llmReady && meta && (
        <div className="mb-4 flex items-center gap-2 rounded-xl border border-warn/30 bg-warn/5 px-3 py-2.5 text-sm text-muted">
          <TriangleAlert className="size-4 text-warn" /> Document reading needs <code className="text-warn">OPENAI_API_KEY</code> on the backend.
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <FileDrop
          accept=".pdf"
          disabled={!llmReady || busy !== null}
          onFiles={([f]) => void extract(f)}
          title="Drop a supplier notice, advisory or bulletin (PDF)"
          hint={`Up to ${bytes(meta?.limits.pdf_max_bytes ?? 4_000_000)}`}
        />
        <div className="rounded-xl border border-line bg-ink-950/40 p-3">
          <p className="eyebrow mb-2">Sample documents</p>
          <ul className="space-y-1.5">
            {(samples ?? []).map((s) => (
              <li key={s.file}>
                <button
                  className="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-ink-800 disabled:opacity-50"
                  disabled={!llmReady || busy !== null}
                  onClick={() => void trySample(s)}
                >
                  <FileText className="mt-0.5 size-4 shrink-0 text-accent" />
                  <span>
                    <span className="block leading-snug">{s.title}</span>
                    <span className="text-[11px] text-faint">{s.issuer}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {busy === "extract" && (
        <div className="mt-6 flex items-center gap-3 rounded-xl border border-line bg-ink-950/40 p-4 text-sm text-muted">
          <Spinner className="size-5 text-accent" />
          Reading {file?.name} and matching entities against the graph…
        </div>
      )}
      {error ? <div className="mt-4"><ErrorBox error={error} /></div> : null}

      {proposal && (
        <div className="mt-6 space-y-4">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <ScanText className="size-4 text-accent" />
            <span className="font-medium">{proposal.document.filename}</span>
            {proposal.document.issuer && <span className="text-muted">from {proposal.document.issuer}</span>}
            {proposal.document.already_ingested && <span className="chip text-warn">already ingested before</span>}
          </div>

          {!proposal.is_supply_risk_document || drafts.length === 0 ? (
            <p className="rounded-xl border border-line bg-ink-950/40 p-4 text-sm text-muted">
              This document does not describe a supply-chain risk event. Nothing to add.
            </p>
          ) : (
            drafts.map((d, i) => (
              <div key={d.proposed_id} className={clsx("rounded-xl border p-4", d.include ? "border-line-strong bg-ink-950/40" : "border-line opacity-60")}>
                <div className="flex flex-wrap items-center gap-3">
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={d.include} onChange={(e) => patch(i, (x) => ({ ...x, include: e.target.checked }))} className="accent-teal-300" />
                    Add to graph
                  </label>
                  <code className="text-[11px] text-faint">{d.proposed_id}</code>
                </div>
                <div className="mt-3 grid gap-3 md:grid-cols-6">
                  <label className="space-y-1 md:col-span-3">
                    <span className="text-[11px] text-faint">Title</span>
                    <input className="input" value={d.title} onChange={(e) => patch(i, (x) => ({ ...x, title: e.target.value }))} />
                  </label>
                  <label className="space-y-1">
                    <span className="text-[11px] text-faint">Category</span>
                    <select className="input" value={d.category} onChange={(e) => patch(i, (x) => ({ ...x, category: e.target.value }))}>
                      {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
                    </select>
                  </label>
                  <label className="space-y-1">
                    <span className="text-[11px] text-faint">Severity</span>
                    <select className="input" value={d.severity} onChange={(e) => patch(i, (x) => ({ ...x, severity: Number(e.target.value) }))}>
                      {[1, 2, 3, 4, 5].map((s) => <option key={s}>{s}</option>)}
                    </select>
                  </label>
                  <div />
                  <label className="space-y-1">
                    <span className="text-[11px] text-faint">Start date</span>
                    <input type="date" className="input" value={d.start_date ?? ""} onChange={(e) => patch(i, (x) => ({ ...x, start_date: e.target.value || null }))} />
                  </label>
                  <label className="space-y-1">
                    <span className="text-[11px] text-faint">Expected end</span>
                    <input type="date" className="input" value={d.expected_end_date ?? ""} onChange={(e) => patch(i, (x) => ({ ...x, expected_end_date: e.target.value || null }))} />
                  </label>
                  <p className="text-xs leading-relaxed text-muted md:col-span-4 md:self-end">{d.summary}</p>
                </div>

                <p className="eyebrow mb-2 mt-4">Affected entities</p>
                <ul className="space-y-2">
                  {d.targets.map((t, ti) => (
                    <li key={ti} className="grid items-start gap-2 rounded-lg border border-line bg-ink-900/60 p-2.5 md:grid-cols-[1fr_320px]">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 text-sm">
                          <span className="font-medium">{t.name}</span>
                          <StatusPill status={t.status === "matched" ? "available" : t.status === "ambiguous" ? "constrained" : "disrupted"} />
                        </div>
                        <p className="mt-1 flex gap-1.5 text-xs text-muted">
                          <Quote className="size-3 shrink-0 text-faint" /> {t.evidence}
                        </p>
                        {!t.chosen && t.candidates.length > 0 && (
                          <div className="mt-2 flex flex-wrap gap-1">
                            {t.candidates.slice(0, 5).map((c) => (
                              <button
                                key={c.id}
                                className="chip hover:bg-ink-700"
                                onClick={() => patch(i, (x) => ({ ...x, targets: x.targets.map((y, k) => (k === ti ? { ...y, chosen: c } : y)) }))}
                              >
                                <KindDot kind={c.kind} /> {c.name}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                      <EntityPicker
                        kinds={["Region", "Port", "Supplier"]}
                        value={t.chosen}
                        placeholder="Match to a region, port or supplier…"
                        onChange={(e) => patch(i, (x) => ({ ...x, targets: x.targets.map((y, k) => (k === ti ? { ...y, chosen: e } : y)) }))}
                      />
                    </li>
                  ))}
                </ul>
              </div>
            ))
          )}

          {drafts.length > 0 && !committed && (
            <div className="flex flex-wrap items-center gap-3">
              <button className="btn btn-primary" disabled={!ready.length || problems.length > 0 || busy !== null} onClick={commit}>
                {busy === "commit" ? <Spinner /> : <Send className="size-4" />} Add {ready.length} disruption(s) to the graph
              </button>
              {problems.map((p) => (
                <span key={p} className="text-xs text-warn">{p}</span>
              ))}
            </div>
          )}

          {committed && (
            <div className="rounded-xl border border-ok/30 bg-ok/5 p-4 text-sm">
              <p className="flex items-center gap-2 font-medium text-ok">
                <CheckCircle2 className="size-4" /> Added to the graph as {committed.document_id}
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                {committed.disruptions.map((d) => (
                  <button key={d.id} className="chip hover:bg-ink-700" onClick={() => open(d.id)}>
                    <KindDot kind="Disruption" /> {d.id} <span className="text-faint">· {d.affects} affected</span>
                  </button>
                ))}
              </div>
              <p className="mt-2 text-xs text-muted">
                {committed.passages.indexed && committed.passages.passages > 0
                  ? `Indexed ${committed.passages.passages} passages for search (${committed.passages.linked ?? 0} linked to graph entities). `
                  : committed.passages.warning
                    ? `${committed.passages.warning}. `
                    : ""}
                The overview, simulator and analyst now include these disruptions.
              </p>
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

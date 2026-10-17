"use client";

import clsx from "clsx";
import {
  ArrowUp,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Square,
  Wrench,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { API_BASE } from "@/lib/api";
import { useMeta, useOverview } from "@/lib/hooks";
import { readEventStream } from "@/lib/sse";
import type { AgentAnswer, Outcome, ToolTrace } from "@/lib/types";

import { useAnalyst } from "./providers";
import { EntityChip, OutcomeBadge, PathChain, Spinner } from "./ui";

interface LiveTool {
  id: string;
  tool: string;
  arguments: Record<string, unknown>;
  done?: ToolTrace;
}

interface Message {
  role: "user" | "assistant";
  content: string;
  answer?: AgentAnswer;
  tools?: LiveTool[];
  error?: string;
  pending?: boolean;
}

const SESSION_KEY = "supplygraph.analyst.session";

function sessionId(): string {
  try {
    const existing = sessionStorage.getItem(SESSION_KEY);
    if (existing) return existing;
    const id = crypto.randomUUID().replace(/-/g, "").slice(0, 24);
    sessionStorage.setItem(SESSION_KEY, id);
    return id;
  } catch {
    return crypto.randomUUID().replace(/-/g, "").slice(0, 24);
  }
}

export function AnalystPanel() {
  const analyst = useAnalyst();
  const { data: meta } = useMeta();
  const { data: overview } = useOverview();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const llmReady = meta?.llm_configured ?? false;

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (!analyst.isOpen || running) return;
    const prompt = analyst.consume();
    if (prompt) void send(prompt);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analyst.isOpen, analyst.pending]);

  const update = (fn: (m: Message) => Message) =>
    setMessages((all) => [...all.slice(0, -1), fn(all[all.length - 1])]);

  async function send(question: string) {
    const text = question.trim();
    if (!text || running || !llmReady) return;
    const history = messages
      .filter((m) => !m.pending && !m.error && m.content)
      .map((m) => ({ role: m.role, content: m.content }));
    setMessages((all) => [
      ...all,
      { role: "user", content: text },
      { role: "assistant", content: "", tools: [], pending: true },
    ]);
    setInput("");
    setRunning(true);
    const controller = new AbortController();
    abort.current = controller;
    try {
      const res = await fetch(`${API_BASE}/v1/agent/stream`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: text, session_id: sessionId(), history: history.slice(-10) }),
        signal: controller.signal,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `Request failed (${res.status})`);
      }
      for await (const { event, data } of readEventStream(res, controller.signal)) {
        const payload = data as Record<string, unknown>;
        if (event === "tool_start") {
          update((m) => ({
            ...m,
            tools: [
              ...(m.tools ?? []),
              { id: String(payload.id), tool: String(payload.tool), arguments: payload.arguments as Record<string, unknown> },
            ],
          }));
        } else if (event === "tool_end") {
          update((m) => ({
            ...m,
            tools: (m.tools ?? []).map((t) =>
              t.id === payload.id ? { ...t, done: payload as unknown as ToolTrace } : t,
            ),
          }));
        } else if (event === "answer") {
          const answer = payload.answer as AgentAnswer;
          update((m) => ({ ...m, content: answer.answer, answer, pending: false }));
        } else if (event === "error") {
          throw new Error(String(payload.detail ?? "The analyst failed"));
        }
      }
    } catch (err) {
      const aborted = controller.signal.aborted;
      update((m) => ({
        ...m,
        pending: false,
        error: aborted ? "Stopped." : err instanceof Error ? err.message : String(err),
      }));
    } finally {
      setRunning(false);
      abort.current = null;
    }
  }

  if (!analyst.isOpen) return null;

  return (
    <aside
      className="fixed inset-y-0 right-0 z-50 flex w-full max-w-[480px] flex-col border-l border-line bg-ink-900/95 shadow-2xl backdrop-blur-xl animate-fade-in"
      aria-label="Analyst"
    >
      <header className="flex items-center gap-3 border-b border-line px-4 py-3">
        <span className="grid size-8 place-items-center rounded-lg bg-gradient-to-br from-accent/20 to-accent-2/20 ring-1 ring-accent/30">
          <Sparkles className="size-4 text-accent" />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold">Risk analyst</p>
          <p className="truncate text-[11px] text-faint">
            The model picks graph tools · FalkorDB answers · every claim is checked
          </p>
        </div>
        <div className="ml-auto flex items-center gap-1">
          {messages.length > 0 && (
            <button
              className="btn btn-ghost h-8 px-2"
              title="New conversation"
              onClick={() => {
                abort.current?.abort();
                setMessages([]);
                try {
                  sessionStorage.removeItem(SESSION_KEY);
                } catch {}
              }}
            >
              <RotateCcw className="size-4" />
            </button>
          )}
          <button className="btn btn-ghost h-8 px-2" onClick={analyst.close} aria-label="Close analyst">
            <X className="size-4" />
          </button>
        </div>
      </header>

      <div ref={scroller} className="flex-1 space-y-5 overflow-y-auto px-4 py-5">
        {!llmReady && meta && (
          <div className="rounded-xl border border-warn/30 bg-warn/5 p-4 text-sm text-muted">
            The analyst needs <code className="text-warn">OPENAI_API_KEY</code> on the backend. Every
            other page works without it.
          </div>
        )}
        {messages.length === 0 && llmReady && (
          <div>
            <p className="text-sm text-muted">
              Ask about disruptions, exposure, alternates or lead times. The analyst only answers from
              graph traversals and shows the path behind each claim.
            </p>
            <div className="mt-4 space-y-2">
              {(overview?.suggestions ?? []).map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  className="panel panel-hover block w-full px-3.5 py-2.5 text-left text-sm text-fg"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="ml-10 rounded-2xl rounded-tr-sm bg-ink-700/80 px-4 py-2.5 text-sm">
              {m.content}
            </div>
          ) : (
            <AssistantMessage key={i} message={m} />
          ),
        )}
      </div>

      <form
        className="border-t border-line p-3"
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
      >
        <div className="flex items-end gap-2 rounded-xl border border-line bg-ink-950/70 p-2 focus-within:border-accent/50">
          <textarea
            className="max-h-40 min-h-[40px] flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-faint"
            placeholder={llmReady ? "Ask the analyst…" : "Analyst unavailable"}
            value={input}
            disabled={!llmReady}
            rows={1}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send(input);
              }
            }}
          />
          {running ? (
            <button type="button" className="btn h-9 px-2.5" onClick={() => abort.current?.abort()} aria-label="Stop">
              <Square className="size-4" />
            </button>
          ) : (
            <button className="btn btn-primary h-9 px-2.5" disabled={!input.trim() || !llmReady} aria-label="Send">
              <ArrowUp className="size-4" />
            </button>
          )}
        </div>
      </form>
    </aside>
  );
}

function AssistantMessage({ message }: { message: Message }) {
  const [showTools, setShowTools] = useState(false);
  const tools = message.tools ?? [];
  const answer = message.answer;
  return (
    <div className="space-y-3">
      {tools.length > 0 && (
        <div className="rounded-xl border border-line bg-ink-950/50">
          <button
            className="flex w-full items-center gap-2 px-3 py-2 text-xs text-muted"
            onClick={() => setShowTools((s) => !s)}
          >
            <Wrench className="size-3.5" />
            {message.pending ? "Querying the graph" : `${tools.length} graph quer${tools.length === 1 ? "y" : "ies"}`}
            {message.pending && <Spinner className="size-3" />}
            <ChevronDown className={clsx("ml-auto size-3.5 transition", (showTools || message.pending) && "rotate-180")} />
          </button>
          {(showTools || message.pending) && (
            <ol className="space-y-1.5 border-t border-line px-3 py-2">
              {tools.map((t) => (
                <li key={t.id} className="text-xs">
                  <div className="flex items-center gap-2">
                    {t.done ? (
                      t.done.error ? (
                        <CircleAlert className="size-3.5 text-danger" />
                      ) : (
                        <CheckCircle2 className="size-3.5 text-ok" />
                      )
                    ) : (
                      <Spinner className="size-3.5 text-accent" />
                    )}
                    <span className="font-mono text-fg">{t.tool}</span>
                    <OutcomeBadge outcome={(t.done?.outcome ?? null) as Outcome | null} />
                    {t.done && <span className="ml-auto num text-faint">{t.done.elapsed_ms} ms</span>}
                  </div>
                  <p className="mt-0.5 truncate pl-5 font-mono text-[11px] text-faint">
                    {JSON.stringify(t.arguments)}
                  </p>
                  {t.done?.error && <p className="pl-5 text-[11px] text-danger">{t.done.error}</p>}
                </li>
              ))}
            </ol>
          )}
        </div>
      )}

      {message.error && (
        <div className="rounded-xl border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">
          {message.error}
        </div>
      )}

      {answer && (
        <div className="space-y-3 animate-fade-in">
          <div
            className={clsx(
              "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px]",
              answer.grounded ? "border-ok/30 bg-ok/10 text-ok" : "border-warn/30 bg-warn/10 text-warn",
            )}
            title={answer.violations.join("\n")}
          >
            <ShieldCheck className="size-3.5" />
            {answer.grounded ? "Grounded in graph results" : "Draft replaced with raw graph results"}
          </div>
          <div className="whitespace-pre-wrap text-sm leading-relaxed text-fg">{answer.answer}</div>
          {answer.paths.length > 0 && (
            <div className="space-y-2 rounded-xl border border-line bg-ink-950/40 p-3">
              <p className="eyebrow">Graph paths</p>
              {answer.paths.slice(0, 4).map((p, i) => (
                <PathChain key={i} path={p} />
              ))}
            </div>
          )}
          {answer.entities.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {answer.entities.slice(0, 14).map((e) => (
                <EntityChip key={e.id} entity={e} />
              ))}
            </div>
          )}
          <p className="text-[11px] text-faint">
            {answer.model} · {(answer.elapsed_ms / 1000).toFixed(1)}s ·{" "}
            {answer.tool_calls.length} tool calls
          </p>
        </div>
      )}
    </div>
  );
}

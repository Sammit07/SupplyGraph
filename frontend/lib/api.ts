"use client";

import { mutate } from "swr";

/** Same-origin proxy to the private backend service. */
export const API_BASE = "/api/sg";
export const WORKSPACE_COOKIE = "sg_workspace";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public body?: unknown,
  ) {
    super(message);
  }
}

async function parseError(res: Response): Promise<ApiError> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    body = undefined;
  }
  const b = (body ?? {}) as { error?: string; detail?: unknown };
  let message = res.statusText || "Request failed";
  if (typeof b.detail === "string") message = b.detail;
  else if (Array.isArray(b.detail)) {
    message = b.detail
      .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`)
      .join("; ");
  }
  return new ApiError(res.status, b.error ?? String(res.status), message, body);
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body && !(init.body instanceof FormData) ? { "content-type": "application/json" } : {}),
      ...init?.headers,
    },
    cache: "no-store",
  });
  if (res.status === 401 && typeof window !== "undefined") {
    // Session expired: a hard navigation drops all client state before re-authenticating.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
  }
  if (!res.ok) throw await parseError(res);
  return (await res.json()) as T;
}

/** Like `api`, but for endpoints that return a JSON report on 4xx (imports). */
export async function apiReport<T>(path: string, init: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { ...init, cache: "no-store" });
  const body = await res.json().catch(() => undefined);
  if (res.ok || (res.status === 422 && body && typeof body === "object" && "issues" in body)) {
    return body as T;
  }
  const b = (body ?? {}) as { error?: string; detail?: string };
  throw new ApiError(res.status, b.error ?? String(res.status), b.detail ?? res.statusText, body);
}

export const fetcher = <T,>(path: string) => api<T>(path);

export function runTool<D = Record<string, unknown>>(name: string, args: Record<string, unknown>) {
  return api<import("./types").ToolResult<D>>(`/v1/tools/${name}`, {
    method: "POST",
    body: JSON.stringify(args),
  });
}

export function currentWorkspace(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp(`(?:^|; )${WORKSPACE_COOKIE}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

/** Switch workspace: the proxy forwards this cookie as X-Tenant. */
export async function setWorkspace(name: string): Promise<void> {
  const secure = window.location.protocol === "https:" ? "; secure" : "";
  document.cookie = `${WORKSPACE_COOKIE}=${encodeURIComponent(name)}; path=/; max-age=31536000; samesite=lax${secure}`;
  await mutate(() => true, undefined, { revalidate: true });
}

export const WORKSPACE_RE = /^[a-z0-9][a-z0-9_-]{0,62}$/;

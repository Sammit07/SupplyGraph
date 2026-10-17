import { cookies } from "next/headers";
import type { NextRequest } from "next/server";

import { WORKSPACE_COOKIE, backendHeaders, backendUrl } from "@/lib/server/backend";
import { SESSION_COOKIE, authEnabled, verifySessionToken } from "@/lib/server/session";

// Agent runs and PDF extraction can take minutes.
export const maxDuration = 300;
export const dynamic = "force-dynamic";

const ALLOWED = /^(v1(\/[A-Za-z0-9_.:\-]+)*|readyz)$/;
const FORWARD_REQUEST_HEADERS = ["content-type", "accept", "x-session-id", "x-confirm"];
const FORWARD_RESPONSE_HEADERS = [
  "content-type",
  "content-disposition",
  "cache-control",
  "x-request-id",
];

type Context = { params: Promise<{ path: string[] }> };

async function forward(request: NextRequest, context: Context): Promise<Response> {
  const { path } = await context.params;
  const subPath = path.map((p) => encodeURIComponent(p)).join("/");
  if (!ALLOWED.test(path.join("/"))) {
    return Response.json({ error: "not_found" }, { status: 404 });
  }

  const jar = await cookies();
  if (authEnabled() && !verifySessionToken(jar.get(SESSION_COOKIE)?.value)) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  const requestId = request.headers.get("x-request-id") ?? crypto.randomUUID();
  const headers = backendHeaders(jar.get(WORKSPACE_COOKIE)?.value, requestId);
  for (const name of FORWARD_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  const hasBody = !["GET", "HEAD"].includes(request.method);
  let upstream: Response;
  try {
    upstream = await fetch(new URL(subPath + request.nextUrl.search, backendUrl()), {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
      // Required by undici when streaming a request body.
      ...(hasBody ? { duplex: "half" } : {}),
      cache: "no-store",
      redirect: "manual",
      signal: request.signal,
    } as RequestInit);
  } catch (error) {
    if (request.signal.aborted) return new Response(null, { status: 499 });
    console.error("backend unreachable", { requestId, error: String(error) });
    return Response.json(
      { error: "backend_unreachable", detail: "The analysis service is not reachable." },
      { status: 502 },
    );
  }

  const responseHeaders = new Headers();
  for (const name of FORWARD_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  if (upstream.headers.get("content-type")?.startsWith("text/event-stream")) {
    responseHeaders.set("cache-control", "no-cache, no-transform");
    responseHeaders.set("x-accel-buffering", "no");
  }
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export { forward as DELETE, forward as GET, forward as PATCH, forward as POST };

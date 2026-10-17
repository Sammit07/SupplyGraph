import "server-only";

export const WORKSPACE_COOKIE = "sg_workspace";

/**
 * Base URL of the private FastAPI service.
 * On Vercel the `backend` service binding injects BACKEND_URL; locally set it in .env.local.
 */
export function backendUrl(): string {
  const url = process.env.BACKEND_URL;
  if (!url) throw new Error("BACKEND_URL is not configured");
  return url.endsWith("/") ? url : `${url}/`;
}

/** Headers every backend call carries: shared secret and workspace. */
export function backendHeaders(workspace: string | undefined, requestId: string): Headers {
  const headers = new Headers({ "x-request-id": requestId });
  const key = process.env.SUPPLYGRAPH_API_KEY;
  if (key) headers.set("x-api-key", key);
  if (workspace) headers.set("x-tenant", workspace);
  return headers;
}

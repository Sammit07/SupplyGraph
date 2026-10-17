import { cookies } from "next/headers";

import {
  SESSION_COOKIE,
  authEnabled,
  createSessionToken,
  passwordMatches,
} from "@/lib/server/session";

export async function POST(request: Request): Promise<Response> {
  if (!authEnabled()) return Response.json({ ok: true });
  let password = "";
  try {
    const body = (await request.json()) as { password?: unknown };
    password = typeof body.password === "string" ? body.password : "";
  } catch {
    return Response.json({ error: "invalid_request" }, { status: 400 });
  }
  if (!passwordMatches(password)) {
    // Uniform delay blunts online guessing.
    await new Promise((resolve) => setTimeout(resolve, 600));
    return Response.json({ error: "invalid_password" }, { status: 401 });
  }
  const { token, maxAge } = createSessionToken();
  (await cookies()).set(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge,
  });
  return Response.json({ ok: true });
}

import { cookies } from "next/headers";

import { SESSION_COOKIE } from "@/lib/server/session";

export async function POST(): Promise<Response> {
  (await cookies()).delete(SESSION_COOKIE);
  return Response.json({ ok: true });
}

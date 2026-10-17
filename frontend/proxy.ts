import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE, authEnabled, verifySessionToken } from "@/lib/server/session";

/** Optional password gate (enabled by APP_PASSWORD). */
export function proxy(request: NextRequest) {
  if (!authEnabled()) return NextResponse.next();
  if (verifySessionToken(request.cookies.get(SESSION_COOKIE)?.value)) return NextResponse.next();

  const { pathname, search } = request.nextUrl;
  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const login = new URL("/login", request.url);
  login.searchParams.set("next", pathname + search);
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/((?!login|api/auth|_next/static|_next/image|favicon.ico|icon.svg).*)"],
};

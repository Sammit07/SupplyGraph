import "server-only";

import { createHmac, timingSafeEqual } from "node:crypto";

export const SESSION_COOKIE = "sg_session";
const MAX_AGE_SECONDS = 60 * 60 * 12;

/** Password protection is on when APP_PASSWORD is set. */
export function authEnabled(): boolean {
  return Boolean(process.env.APP_PASSWORD);
}

function secret(): string {
  const value = process.env.SESSION_SECRET;
  if (!value || value.length < 32) {
    throw new Error("SESSION_SECRET must be set (32+ characters) when APP_PASSWORD is used");
  }
  return value;
}

function sign(payload: string): string {
  return createHmac("sha256", secret()).update(payload).digest("base64url");
}

export function createSessionToken(now = Date.now()): { token: string; maxAge: number } {
  const expires = Math.floor(now / 1000) + MAX_AGE_SECONDS;
  const payload = String(expires);
  return { token: `${payload}.${sign(payload)}`, maxAge: MAX_AGE_SECONDS };
}

export function verifySessionToken(token: string | undefined, now = Date.now()): boolean {
  if (!token) return false;
  const [payload, signature] = token.split(".");
  if (!payload || !signature) return false;
  const expected = Buffer.from(sign(payload));
  const given = Buffer.from(signature);
  if (expected.length !== given.length || !timingSafeEqual(expected, given)) return false;
  return Number(payload) * 1000 > now;
}

export function passwordMatches(candidate: string): boolean {
  const expected = Buffer.from(process.env.APP_PASSWORD ?? "");
  const given = Buffer.from(candidate);
  return expected.length > 0 && expected.length === given.length && timingSafeEqual(expected, given);
}

"use client";

import useSWR from "swr";

import type { Meta, Overview } from "./types";

export function useMeta(enabled = true) {
  return useSWR<Meta>(enabled ? "/v1/meta" : null, { dedupingInterval: 30_000 });
}

export function useOverview() {
  return useSWR<Overview>("/v1/overview", { refreshInterval: 60_000 });
}

export function useWorkspaces() {
  return useSWR<{ workspaces: string[]; default: string }>("/v1/workspaces");
}

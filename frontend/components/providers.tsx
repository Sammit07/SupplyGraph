"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { SWRConfig } from "swr";

import { ApiError, fetcher } from "@/lib/api";

interface InspectorState {
  entityId: string | null;
  open: (id: string) => void;
  close: () => void;
}

interface AnalystState {
  isOpen: boolean;
  pending: string | null;
  open: (prompt?: string) => void;
  close: () => void;
  consume: () => string | null;
}

const InspectorContext = createContext<InspectorState | null>(null);
const AnalystContext = createContext<AnalystState | null>(null);

export function Providers({ children }: { children: ReactNode }) {
  const [entityId, setEntityId] = useState<string | null>(null);
  const [analystOpen, setAnalystOpen] = useState(false);
  const [pending, setPending] = useState<string | null>(null);

  const inspector = useMemo<InspectorState>(
    () => ({ entityId, open: setEntityId, close: () => setEntityId(null) }),
    [entityId],
  );
  const consume = useCallback(() => {
    const value = pending;
    setPending(null);
    return value;
  }, [pending]);
  const analyst = useMemo<AnalystState>(
    () => ({
      isOpen: analystOpen,
      pending,
      open: (prompt?: string) => {
        if (prompt) setPending(prompt);
        setAnalystOpen(true);
      },
      close: () => setAnalystOpen(false),
      consume,
    }),
    [analystOpen, pending, consume],
  );

  return (
    <SWRConfig
      value={{
        fetcher,
        revalidateOnFocus: false,
        keepPreviousData: true,
        shouldRetryOnError: (err) => !(err instanceof ApiError && err.status < 500),
        errorRetryCount: 2,
      }}
    >
      <InspectorContext.Provider value={inspector}>
        <AnalystContext.Provider value={analyst}>{children}</AnalystContext.Provider>
      </InspectorContext.Provider>
    </SWRConfig>
  );
}

export function useInspector(): InspectorState {
  const ctx = useContext(InspectorContext);
  if (!ctx) throw new Error("useInspector outside Providers");
  return ctx;
}

export function useAnalyst(): AnalystState {
  const ctx = useContext(AnalystContext);
  if (!ctx) throw new Error("useAnalyst outside Providers");
  return ctx;
}

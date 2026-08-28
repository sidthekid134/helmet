"use client";

import { useCallback, useEffect, useState } from "react";
import type { ApiEnvelope } from "@/lib/contracts";

export type AsyncState<T> =
  | { status: "loading"; data: null; error: null }
  | { status: "success"; data: T; error: null }
  | { status: "error"; data: null; error: Error };

export function useApi<T>(loader: () => Promise<ApiEnvelope<T>>) {
  const [state, setState] = useState<AsyncState<T>>({
    status: "loading",
    data: null,
    error: null,
  });

  const load = useCallback(async () => {
    setState({ status: "loading", data: null, error: null });
    try {
      const response = await loader();
      setState({ status: "success", data: response.data, error: null });
    } catch (error) {
      setState({
        status: "error",
        data: null,
        error: error instanceof Error ? error : new Error("Unknown API error"),
      });
    }
  }, [loader]);

  useEffect(() => {
    let active = true;
    loader().then(
      (response) => {
        if (active) setState({ status: "success", data: response.data, error: null });
      },
      (error: unknown) => {
        if (active) {
          setState({
            status: "error",
            data: null,
            error: error instanceof Error ? error : new Error("Unknown API error"),
          });
        }
      },
    );
    return () => {
      active = false;
    };
  }, [loader]);

  return { ...state, retry: load };
}

"use client";

import { ErrorState } from "@/components/states";

export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <ErrorState error={error} retry={reset} />;
}

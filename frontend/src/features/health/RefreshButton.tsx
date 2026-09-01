"use client";

import { useRouter } from "next/navigation";
import { useTransition } from "react";

/**
 * Re-runs the server render so the panel re-queries the backend.
 *
 * The pending state comes from useTransition rather than a manual boolean, so
 * the button cannot get stuck in a "refreshing" state if the request fails.
 */
export function RefreshButton({ label = "Refresh" }: { label?: string }) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();

  return (
    <button
      type="button"
      disabled={isPending}
      onClick={() => startTransition(() => router.refresh())}
      className="mt-4 rounded-md border border-black/15 px-3 py-1.5 text-sm hover:bg-black/5 disabled:opacity-50 dark:border-white/20 dark:hover:bg-white/10"
    >
      {isPending ? "Refreshing…" : label}
    </button>
  );
}

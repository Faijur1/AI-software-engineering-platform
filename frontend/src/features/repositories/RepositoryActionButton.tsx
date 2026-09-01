"use client";

import { useState, useTransition } from "react";

import type { ActionResult } from "@/features/repositories/actions";

/**
 * A button that runs a server action and shows what happened.
 *
 * Deliberately not optimistic: connecting can genuinely fail — GitHub may have
 * revoked access since the list was rendered — and showing success before the
 * server confirms it would be showing something untrue.
 */
export function RepositoryActionButton({
  action,
  label,
  pendingLabel,
  variant = "primary",
}: {
  action: () => Promise<ActionResult>;
  label: string;
  pendingLabel: string;
  variant?: "primary" | "secondary";
}) {
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  const base =
    "rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-50";
  const styles =
    variant === "primary"
      ? "bg-black text-white hover:opacity-85 dark:bg-white dark:text-black"
      : "border border-black/15 hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10";

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        disabled={pending}
        className={`${base} ${styles}`}
        onClick={() => {
          setError(null);
          startTransition(async () => {
            const result = await action();
            setError(result.error);
          });
        }}
      >
        {pending ? pendingLabel : label}
      </button>
      {error && (
        <p role="alert" className="max-w-xs text-right text-xs text-red-700 dark:text-red-400">
          {error}
        </p>
      )}
    </div>
  );
}

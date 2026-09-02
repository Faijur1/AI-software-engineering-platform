"use client";

import { useState } from "react";

import { setCloudPermission } from "@/features/repositories/actions";

/**
 * Grants or withdraws permission to send this repository's code to a hosted
 * model.
 *
 * The wording is the feature. A control labelled "use better model" would hide
 * what is actually being decided: answering a question sends retrieved source
 * code to whoever generates the answer, and when that is a hosted API the code
 * leaves the machine. So the label names the disclosure, not the benefit.
 *
 * Off is not an error state and is not styled as one. Local-only is a
 * legitimate choice — for a private repository it may be the only acceptable
 * one — and a warning colour on the safe option would push people toward
 * granting permission to make a red badge go away.
 */
export function CloudPermissionToggle({
  repositoryId,
  allowed,
  grantedAt,
}: {
  repositoryId: string;
  allowed: boolean;
  grantedAt: string | null;
}) {
  // Optimistic, so the switch responds immediately; reverted if the server
  // disagrees, because a toggle that lies about a permission is worse than a
  // slow one.
  const [optimistic, setOptimistic] = useState(allowed);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const change = async (next: boolean) => {
    setOptimistic(next);
    setSaving(true);
    setError(null);

    const result = await setCloudPermission(repositoryId, next);

    setSaving(false);
    if (result.error !== null) {
      setOptimistic(!next);
      setError(result.error);
    }
  };

  return (
    <div className="flex flex-col items-start gap-1">
      <label className="flex cursor-pointer items-center gap-2 text-xs">
        <input
          type="checkbox"
          checked={optimistic}
          disabled={saving}
          onChange={(event) => void change(event.target.checked)}
          className="h-3.5 w-3.5 cursor-pointer accent-black disabled:cursor-wait dark:accent-white"
        />
        <span className="text-black/70 dark:text-white/70">
          Send this repository&rsquo;s code to the hosted model
        </span>
      </label>

      <p className="max-w-md text-[11px] leading-snug text-black/50 dark:text-white/50">
        {optimistic ? (
          <>
            Retrieved code from this repository is sent to the configured model
            provider when you ask a question.
            {grantedAt !== null && (
              <> Allowed since {new Date(grantedAt).toLocaleDateString()}.</>
            )}{" "}
            Turning this off applies to future questions; it cannot recall what
            was already sent.
          </>
        ) : (
          <>
            Nothing from this repository leaves this machine. Questions are
            answered by the local model, which cites less reliably.
          </>
        )}
      </p>

      {error !== null && (
        <p role="alert" className="max-w-md text-xs text-red-700 dark:text-red-400">
          {error}
        </p>
      )}
    </div>
  );
}

"use client";

import { useState } from "react";

import { decidePatch } from "@/features/agent/actions";
import type { Patch } from "@/features/agent/types";

/**
 * A proposed change, its diff, and the human approval gate.
 *
 * Two things this deliberately refuses to blur:
 *
 * **Validated and approved are different facts.** A patch can pass its tests
 * and still be unapproved, and can be approved having never been validated.
 * The UI shows both independently rather than collapsing them into one
 * reassuring badge.
 *
 * **Null validation is not a pass.** An unvalidated patch says so in plain
 * words. Rendering it as neutral or absent would let a reader assume the tests
 * were fine.
 */
export function PatchViewer({ patch: initial }: { patch: Patch }) {
  const [patch, setPatch] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const decide = async (approve: boolean) => {
    setPending(true);
    setError(null);
    const result = await decidePatch(patch.id, approve);
    setPending(false);
    if (result.error !== null || result.patch === null) {
      setError(result.error ?? "The decision could not be recorded.");
      return;
    }
    setPatch(result.patch);
  };

  const decided = patch.status !== "proposed";

  return (
    <div className="mt-4 rounded-lg border border-black/10 p-4 dark:border-white/15">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium">
          {patch.summary || "Proposed change"}
        </span>
        <span
          className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
            patch.status === "approved"
              ? "bg-green-500/15 text-green-700 dark:text-green-400"
              : patch.status === "rejected"
                ? "bg-red-500/15 text-red-700 dark:text-red-400"
                : "bg-black/10 text-black/70 dark:bg-white/15 dark:text-white/70"
          }`}
        >
          {patch.status}
        </span>
      </div>

      <ValidationBanner patch={patch} />

      <pre className="mt-3 max-h-80 overflow-auto rounded bg-black/5 p-3 text-xs dark:bg-white/10">
        <code>
          {patch.diff.split("\n").map((line, index) => (
            <div key={index} className={diffLineClass(line)}>
              {line || " "}
            </div>
          ))}
        </code>
      </pre>

      {error && (
        <p role="alert" className="mt-2 text-xs text-red-700 dark:text-red-400">
          {error}
        </p>
      )}

      {decided ? (
        <p className="mt-3 text-xs text-black/50 dark:text-white/50">
          {patch.status === "approved" ? "Approved" : "Rejected"}
          {patch.approved_at &&
            ` on ${new Date(patch.approved_at).toLocaleString()}`}
          . Stage 1 stops here — nothing is written to the repository.
        </p>
      ) : (
        <div className="mt-3 flex items-center gap-2">
          <button
            type="button"
            onClick={() => void decide(true)}
            disabled={pending}
            className="rounded-md bg-black px-3 py-1.5 text-sm font-medium text-white transition-opacity hover:opacity-85 disabled:opacity-40 dark:bg-white dark:text-black"
          >
            {pending ? "Recording…" : "Approve"}
          </button>
          <button
            type="button"
            onClick={() => void decide(false)}
            disabled={pending}
            className="rounded-md border border-black/15 px-3 py-1.5 text-sm transition-colors hover:bg-black/5 disabled:opacity-40 dark:border-white/20 dark:hover:bg-white/10"
          >
            Reject
          </button>
        </div>
      )}
    </div>
  );
}

function ValidationBanner({ patch }: { patch: Patch }) {
  if (patch.validated === true) {
    return (
      <p className="mt-2 rounded-md border border-green-500/40 bg-green-500/5 px-3 py-2 text-xs text-green-800 dark:text-green-300">
        Applied cleanly and the test suite passed inside the sandbox.
      </p>
    );
  }
  if (patch.validated === false) {
    return (
      <p className="mt-2 rounded-md border border-red-500/40 bg-red-500/5 px-3 py-2 text-xs text-red-800 dark:text-red-300">
        This patch did not pass validation in the sandbox — it either failed to
        apply or broke the tests.
      </p>
    );
  }
  // Null. Stated plainly, because silence here reads as approval.
  return (
    <p className="mt-2 rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
      Not validated. This patch has not been applied or tested in the sandbox,
      so nothing is known about whether it works.
    </p>
  );
}

function diffLineClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) {
    return "text-black/50 dark:text-white/50";
  }
  if (line.startsWith("@@")) return "text-blue-700 dark:text-blue-400";
  if (line.startsWith("+")) return "bg-green-500/15 text-green-800 dark:text-green-300";
  if (line.startsWith("-")) return "bg-red-500/15 text-red-800 dark:text-red-300";
  return "";
}

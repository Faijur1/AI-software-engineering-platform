/**
 * Live backend/dependency status, fetched on the server.
 *
 * This renders whatever the backend actually reports — there is no optimistic
 * or placeholder state. If the backend is unreachable, that is shown as an
 * error with a retry, not as a healthy-looking default.
 */

import { RefreshButton } from "@/features/health/RefreshButton";
import type { HealthResponse } from "@/features/health/types";
import { ApiError, apiFetch } from "@/lib/api";

const panelClass = "rounded-lg border border-black/10 p-6 dark:border-white/15";

export function SystemStatusSkeleton() {
  return (
    <div className={panelClass}>
      <p className="text-sm text-black/60 dark:text-white/60">Checking system status…</p>
    </div>
  );
}

function UnreachablePanel({ message }: { message: string }) {
  return (
    <div role="alert" className="rounded-lg border border-red-500/40 bg-red-500/5 p-6">
      <h2 className="font-medium text-red-700 dark:text-red-400">Backend unreachable</h2>
      <p className="mt-1 text-sm text-black/70 dark:text-white/70">{message}</p>
      <p className="mt-2 text-sm text-black/50 dark:text-white/50">
        Start it with{" "}
        <code className="rounded bg-black/5 px-1 py-0.5 dark:bg-white/10">
          uvicorn app.main:app --reload
        </code>{" "}
        in <code className="rounded bg-black/5 px-1 py-0.5 dark:bg-white/10">backend/</code>.
      </p>
      <RefreshButton label="Retry" />
    </div>
  );
}

export async function SystemStatusPanel() {
  let health: HealthResponse;
  try {
    health = await apiFetch<HealthResponse>("/health");
  } catch (error) {
    return (
      <UnreachablePanel
        message={
          error instanceof ApiError
            ? error.message
            : "An unexpected error occurred while contacting the backend."
        }
      />
    );
  }

  const healthy = health.status === "ok";

  return (
    <div className={panelClass}>
      <div className="flex items-center justify-between gap-4">
        <h2 className="font-medium">System status</h2>
        <span
          className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
            healthy
              ? "bg-green-500/15 text-green-700 dark:text-green-400"
              : "bg-amber-500/15 text-amber-700 dark:text-amber-400"
          }`}
        >
          {healthy ? "Operational" : "Degraded"}
        </span>
      </div>

      <p className="mt-1 text-sm text-black/50 dark:text-white/50">
        Environment: {health.environment}
      </p>

      <ul className="mt-4 divide-y divide-black/5 dark:divide-white/10">
        {Object.entries(health.dependencies).map(([name, dep]) => (
          <li key={name} className="flex items-center justify-between py-2 text-sm">
            <span className="capitalize">{name}</span>
            <span className="flex items-center gap-3">
              {dep.latency_ms !== null && (
                <span className="tabular-nums text-black/50 dark:text-white/50">
                  {dep.latency_ms.toFixed(1)} ms
                </span>
              )}
              <span
                className={
                  dep.status === "ok"
                    ? "text-green-700 dark:text-green-400"
                    : "text-red-700 dark:text-red-400"
                }
              >
                {dep.status === "ok" ? "ok" : (dep.error ?? "unavailable")}
              </span>
            </span>
          </li>
        ))}
      </ul>

      <RefreshButton />
    </div>
  );
}

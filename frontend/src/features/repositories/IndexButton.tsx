"use client";

import { useEffect, useState } from "react";

import {
  readJob,
  refreshRepositories,
  startIndexing,
} from "@/features/repositories/indexActions";
import type { Job } from "@/features/repositories/jobTypes";

// Indexing runs for minutes, so the client polls rather than holding a socket
// open. Two seconds feels live without hammering the API.
const POLL_INTERVAL_MS = 2000;

/**
 * Starts indexing and shows real progress reported by the backend.
 *
 * Everything displayed is a value the server sent. There is no simulated
 * progress: a bar that advances on a timer while the job is actually stuck is
 * a lie the user has no way to detect.
 */
export function IndexButton({
  repositoryId,
  indexStatus,
}: {
  repositoryId: string;
  indexStatus: string;
}) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  // Polling lives in an effect keyed on the job, so unmounting or starting a
  // different job cancels the previous loop rather than leaving it running.
  useEffect(() => {
    if (jobId === null) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async () => {
      const latest = await readJob(jobId);
      if (cancelled) return;

      if (latest === null) {
        setError("Lost track of the indexing job. Reload to see its state.");
        setJobId(null);
        return;
      }

      setJob(latest);

      if (latest.status === "succeeded" || latest.status === "failed") {
        if (latest.status === "failed") {
          setError(latest.error ?? "Indexing failed.");
        }
        setJobId(null);
        // The repository row now has a new status, commit and timestamp.
        await refreshRepositories();
        return;
      }

      timer = setTimeout(() => void tick(), POLL_INTERVAL_MS);
    };

    void tick();

    return () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [jobId]);

  const begin = async () => {
    setStarting(true);
    setError(null);
    const result = await startIndexing(repositoryId);
    setStarting(false);

    if (result.error !== null || result.job === null) {
      setError(result.error ?? "Indexing could not be started.");
      return;
    }
    setJob(result.job);
    setJobId(result.job.id);
  };

  const running = starting || jobId !== null;

  return (
    <div className="flex flex-col items-end gap-1">
      {running ? (
        <div className="flex items-center gap-3">
          <div
            className="h-1.5 w-24 overflow-hidden rounded-full bg-black/10 dark:bg-white/15"
            role="progressbar"
            aria-valuenow={job?.progress ?? 0}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Indexing progress"
          >
            <div
              className="h-full bg-black transition-[width] duration-500 dark:bg-white"
              style={{ width: `${job?.progress ?? 0}%` }}
            />
          </div>
          <span className="w-28 text-right text-xs text-black/60 dark:text-white/60">
            {job?.stage ?? "queued"}
          </span>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => void begin()}
          className="rounded-md border border-black/15 px-3 py-1.5 text-sm font-medium transition-colors hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10"
        >
          {indexStatus === "indexed" ? "Re-index" : "Index"}
        </button>
      )}

      {error && (
        <p role="alert" className="max-w-xs text-right text-xs text-red-700 dark:text-red-400">
          {error}
        </p>
      )}
    </div>
  );
}

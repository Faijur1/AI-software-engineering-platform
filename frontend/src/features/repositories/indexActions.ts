"use server";

import { revalidatePath } from "next/cache";

import type { Job } from "@/features/repositories/jobTypes";
import { ApiError } from "@/lib/api";
import { authedFetch } from "@/lib/session";

export interface QueueResult {
  job: Job | null;
  error: string | null;
}

/** Queue indexing and return the job to poll. */
export async function startIndexing(repositoryId: string): Promise<QueueResult> {
  try {
    const job = await authedFetch<Job>(`/repositories/${repositoryId}/index`, {
      method: "POST",
    });
    revalidatePath("/repositories");
    return { job, error: null };
  } catch (error) {
    if (error instanceof ApiError) {
      return {
        job: null,
        error:
          error.code === "unauthenticated"
            ? "Your session has expired. Sign in again."
            : error.message,
      };
    }
    return { job: null, error: "Indexing could not be started." };
  }
}

/**
 * Read a job's current state.
 *
 * The client polls this rather than holding a connection open: indexing takes
 * minutes, and a dropped socket should not lose the user's view of progress.
 */
export async function readJob(jobId: string): Promise<Job | null> {
  try {
    return await authedFetch<Job>(`/jobs/${jobId}`);
  } catch {
    return null;
  }
}

/** Refresh the repository list once indexing has finished. */
export async function refreshRepositories(): Promise<void> {
  revalidatePath("/repositories");
}

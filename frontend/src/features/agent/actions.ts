"use server";

import { revalidatePath } from "next/cache";

import type { AgentRun, AgentRunDetail, Patch, Trace } from "@/features/agent/types";
import { ApiError } from "@/lib/api";
import { authedFetch } from "@/lib/session";

export interface StartResult {
  run: AgentRun | null;
  error: string | null;
}

export async function startRun(
  repositoryId: string,
  task: string,
  allowTests: boolean,
): Promise<StartResult> {
  if (!task.trim()) return { run: null, error: null };

  try {
    const run = await authedFetch<AgentRun>("/agents/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        repository_id: repositoryId,
        task,
        max_iterations: 6,
        allow_tests: allowTests,
      }),
    });
    return { run, error: null };
  } catch (error) {
    if (error instanceof ApiError) return { run: null, error: error.message };
    return { run: null, error: "The run could not be started." };
  }
}

export async function readRun(runId: string): Promise<AgentRunDetail | null> {
  try {
    return await authedFetch<AgentRunDetail>(`/agents/runs/${runId}`);
  } catch {
    return null;
  }
}

export async function readTrace(traceId: string): Promise<Trace | null> {
  try {
    return await authedFetch<Trace>(`/traces/${traceId}`);
  } catch {
    return null;
  }
}

export async function readPatch(patchId: string): Promise<Patch | null> {
  try {
    return await authedFetch<Patch>(`/patches/${patchId}`);
  } catch {
    return null;
  }
}

export interface DecisionResult {
  patch: Patch | null;
  error: string | null;
}

/** Approve or reject a patch. A deliberate human action, recorded with an actor. */
export async function decidePatch(
  patchId: string,
  approve: boolean,
): Promise<DecisionResult> {
  try {
    const patch = await authedFetch<Patch>(`/patches/${patchId}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approve }),
    });
    revalidatePath("/repositories");
    return { patch, error: null };
  } catch (error) {
    if (error instanceof ApiError) return { patch: null, error: error.message };
    return { patch: null, error: "The decision could not be recorded." };
  }
}

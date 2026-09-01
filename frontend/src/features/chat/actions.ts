"use server";

import type { ChatResponse } from "@/features/chat/types";
import { ApiError } from "@/lib/api";
import { authedFetch } from "@/lib/session";

export interface AskResult {
  response: ChatResponse | null;
  error: string | null;
}

export async function ask(
  repositoryId: string,
  question: string,
): Promise<AskResult> {
  if (!question.trim()) return { response: null, error: null };

  try {
    const response = await authedFetch<ChatResponse>("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        repository_id: repositoryId,
        question,
        max_sources: 8,
      }),
      // Generation on a local model is slow; the backend's own timeout is
      // shorter, so this only guards against the connection hanging.
      timeoutMs: 180_000,
    });
    return { response, error: null };
  } catch (error) {
    if (error instanceof ApiError) return { response: null, error: error.message };
    return { response: null, error: "The question could not be answered." };
  }
}

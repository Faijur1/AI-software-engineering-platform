"use server";

import type { SearchResponse } from "@/features/search/types";
import { ApiError } from "@/lib/api";
import { authedFetch } from "@/lib/session";

export interface SearchResult {
  response: SearchResponse | null;
  error: string | null;
}

export async function search(
  repositoryId: string,
  query: string,
  includeCandidates = false,
): Promise<SearchResult> {
  if (!query.trim()) return { response: null, error: null };

  try {
    const response = await authedFetch<SearchResponse>(
      `/repositories/${repositoryId}/search`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query,
          limit: 10,
          include_candidates: includeCandidates,
        }),
        // One embedding call plus two indexed lookups; comfortably under this.
        timeoutMs: 30_000,
      },
    );
    return { response, error: null };
  } catch (error) {
    if (error instanceof ApiError) {
      return { response: null, error: error.message };
    }
    return { response: null, error: "Search failed." };
  }
}

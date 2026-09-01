/**
 * Typed client for the backend API.
 *
 * Every call is bounded by a timeout: a hung backend must surface as an error
 * state in the UI, never as a request that spins forever.
 */

import { backendUrl } from "@/lib/config";

const DEFAULT_TIMEOUT_MS = 10_000;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** The error envelope every backend endpoint returns on failure. */
interface ApiErrorBody {
  error?: { code?: string; message?: string; trace_id?: string };
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit & { timeoutMs?: number } = {},
): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, ...init } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let response: Response;
  try {
    response = await fetch(`${backendUrl}${path}`, {
      ...init,
      signal: controller.signal,
      // Health and status data must never be served from a cache.
      cache: "no-store",
      headers: { Accept: "application/json", ...init.headers },
    });
  } catch (cause) {
    if (cause instanceof Error && cause.name === "AbortError") {
      throw new ApiError(`Request to ${path} timed out after ${timeoutMs}ms`);
    }
    throw new ApiError(`Could not reach the backend at ${backendUrl}`);
  } finally {
    clearTimeout(timer);
  }

  const body: unknown = await response.json().catch(() => null);

  // /health deliberately returns 503 with a valid body; callers that care about
  // partial failure inspect the payload, so only surface non-JSON failures here.
  if (!response.ok && body === null) {
    throw new ApiError(`Request to ${path} failed`, response.status);
  }
  if (!response.ok) {
    const err = (body as ApiErrorBody).error;
    if (err) {
      throw new ApiError(err.message ?? "Request failed", response.status, err.code);
    }
  }

  return body as T;
}

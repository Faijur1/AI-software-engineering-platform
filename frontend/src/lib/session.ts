// Server-only by construction: next/headers throws if imported into a client
// component, so the session cookie cannot leak into browser code.
import { cookies } from "next/headers";

import type { User } from "@/features/auth/types";
import { ApiError, apiFetch } from "@/lib/api";

/**
 * Server-side access to the backend as the signed-in user.
 *
 * The session cookie is HttpOnly and set by the backend, so it can only be
 * forwarded from a server component or a server action — never from the
 * browser. Keeping that forwarding in one place means no caller can
 * accidentally query the backend as an anonymous user and render someone
 * else's empty state.
 */
export async function authedFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const cookieHeader = (await cookies()).toString();
  return apiFetch<T>(path, {
    ...options,
    headers: { ...options.headers, Cookie: cookieHeader },
  });
}

/**
 * The signed-in user, or null when there is no valid session.
 *
 * A 401 is an expected outcome here, not a failure: it is how the backend says
 * "nobody is signed in". Any other error is rethrown, so a broken backend shows
 * as an error rather than silently as a signed-out page.
 */
export async function getCurrentUser(): Promise<User | null> {
  try {
    return await authedFetch<User>("/auth/me");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      return null;
    }
    throw error;
  }
}

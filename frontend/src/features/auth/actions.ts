"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { authedFetch } from "@/lib/session";

const SESSION_COOKIE = "aisep_session";

/**
 * Sign out.
 *
 * The backend is told first so it can apply whatever session invalidation it
 * has, then the cookie is cleared locally. Clearing locally alone would leave a
 * still-valid token in circulation.
 */
export async function signOut(): Promise<void> {
  try {
    await authedFetch<void>("/auth/logout", { method: "POST" });
  } catch {
    // An unreachable backend must not trap the user in a signed-in UI: the
    // local cookie is cleared regardless.
  }
  (await cookies()).delete(SESSION_COOKIE);
  redirect("/");
}

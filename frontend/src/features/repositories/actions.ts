"use server";

import { revalidatePath } from "next/cache";

import { ApiError } from "@/lib/api";
import { authedFetch } from "@/lib/session";

export interface ActionResult {
  error: string | null;
}

/**
 * Connect a repository to the platform.
 *
 * Only owner and name are sent: the backend re-fetches the repository with the
 * caller's own GitHub token, which is what authorises the connection. Sending
 * an id the client chose would make the client the authority.
 */
export async function connectRepository(
  owner: string,
  name: string,
): Promise<ActionResult> {
  try {
    await authedFetch("/repositories", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ owner, name }),
    });
  } catch (error) {
    return { error: describe(error, `Could not connect ${owner}/${name}`) };
  }

  revalidatePath("/repositories");
  return { error: null };
}

export async function disconnectRepository(id: string): Promise<ActionResult> {
  try {
    await authedFetch(`/repositories/${id}`, { method: "DELETE" });
  } catch (error) {
    return { error: describe(error, "Could not disconnect that repository") };
  }

  revalidatePath("/repositories");
  return { error: null };
}

/**
 * Grant or withdraw permission to send this repository's code to a hosted model.
 *
 * Per repository and never implied: enabling a provider in configuration opts
 * nothing in. Withdrawal applies to the next question only — it cannot recall
 * anything already sent, and the UI says so rather than implying otherwise.
 */
export async function setCloudPermission(
  id: string,
  allow: boolean,
): Promise<ActionResult> {
  try {
    await authedFetch(`/repositories/${id}/settings`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ allow_cloud_llm: allow }),
    });
  } catch (error) {
    return {
      error: describe(
        error,
        allow
          ? "Could not enable the hosted model for this repository"
          : "Could not withdraw permission for this repository",
      ),
    };
  }

  revalidatePath("/repositories");
  return { error: null };
}

/** Turn a backend error code into something a person can act on. */
function describe(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return fallback;
  switch (error.code) {
    case "unauthenticated":
      return "Your session has expired. Sign in again.";
    case "external_service_error":
      return "GitHub could not be reached. Try again shortly.";
    case "not_found":
      return "That repository no longer exists, or your GitHub access to it was revoked.";
    default:
      return error.message || fallback;
  }
}

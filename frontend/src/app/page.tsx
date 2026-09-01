import Link from "next/link";
import { Suspense } from "react";

import { SignInButton } from "@/features/auth/SignInButton";
import { UserMenu } from "@/features/auth/UserMenu";
import {
  SystemStatusPanel,
  SystemStatusSkeleton,
} from "@/features/health/SystemStatusPanel";
import { getCurrentUser } from "@/lib/session";

// The status panel queries live dependencies and the header reflects the
// current session, so this page is never prerendered.
export const dynamic = "force-dynamic";

interface PageProps {
  searchParams: Promise<{ auth_error?: string }>;
}

/** Backend error codes from the OAuth callback, in language a person can act on. */
const AUTH_ERRORS: Record<string, string> = {
  access_denied: "Sign-in was cancelled on GitHub.",
  invalid_state:
    "That sign-in link had expired or did not originate here. Try signing in again.",
  missing_code: "GitHub did not return an authorisation code. Try again.",
  unauthenticated: "GitHub would not complete the sign-in. Try again.",
  external_service_error: "GitHub could not be reached. Try again shortly.",
  internal_error:
    "Sign-in is not configured on this server. Check GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET and SESSION_SECRET.",
};

export default async function Home({ searchParams }: PageProps) {
  const [user, params] = await Promise.all([getCurrentUser(), searchParams]);
  const authError = params.auth_error
    ? (AUTH_ERRORS[params.auth_error] ?? "Sign-in failed. Try again.")
    : null;

  return (
    <main className="mx-auto max-w-2xl px-6 py-16">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">AI Software Engineering Platform</h1>
          <p className="mt-2 text-sm text-black/60 dark:text-white/60">
            Stage 1 — connect a repository, index it, and ask questions about the
            code.
          </p>
        </div>
        {user && <UserMenu user={user} />}
      </div>

      {authError && (
        <div
          role="alert"
          className="mt-6 rounded-lg border border-red-500/40 bg-red-500/5 p-4 text-sm text-red-800 dark:text-red-300"
        >
          {authError}
        </div>
      )}

      <div className="mt-8">
        {user ? (
          <Link
            href="/repositories"
            className="inline-flex rounded-md bg-black px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-85 dark:bg-white dark:text-black"
          >
            Your repositories
          </Link>
        ) : (
          <SignInButton />
        )}
      </div>

      <div className="mt-8">
        <Suspense fallback={<SystemStatusSkeleton />}>
          <SystemStatusPanel />
        </Suspense>
      </div>

      <section className="mt-10">
        <h2 className="text-sm font-medium text-black/70 dark:text-white/70">
          Not built yet
        </h2>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-black/50 dark:text-white/50">
          <li>Repository indexing and progress (milestones 3–4)</li>
          <li>Hybrid retrieval, chat and the RAG inspector (milestones 5–8)</li>
          <li>Agent, sandbox and patch proposals (milestone 9)</li>
        </ul>
      </section>
    </main>
  );
}

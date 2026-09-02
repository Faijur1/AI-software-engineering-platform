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
            Connect a repository, index it, and ask questions about the code —
            with citations back to the source, and an agent that investigates
            inside a sandbox.
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
          What&rsquo;s here
        </h2>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-black/60 dark:text-white/60">
          <li>
            Indexing with real progress reported by the worker, never simulated
          </li>
          <li>
            Hybrid retrieval — vector and full-text, fused and reranked — with an
            inspector showing every candidate and the scores behind its rank
          </li>
          <li>
            Answers cited back to the retrieved code, from a local model or a
            hosted one, chosen per repository
          </li>
          <li>
            An agent with a hard iteration cap, code-enforced tool permissions,
            full traces, and patches held behind an approval gate
          </li>
          <li>
            Execution isolated in Docker: no network, read-only root, non-root
            user, all capabilities dropped
          </li>
        </ul>
        {/* The project's posture is that measured limits are stated rather than
            hidden, so the landing page says so too instead of only listing
            capabilities. */}
        <p className="mt-3 text-xs leading-relaxed text-black/50 dark:text-white/50">
          Retrieval is measured against a held-out question set. Answer quality
          depends on the model: a small local model cites unreliably, and the
          agent&rsquo;s decisions are the weakest part. What has been measured,
          and what has not, is recorded in <code>docs/README.md</code>.
        </p>
      </section>
    </main>
  );
}

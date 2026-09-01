import { Suspense } from "react";

import {
  SystemStatusPanel,
  SystemStatusSkeleton,
} from "@/features/health/SystemStatusPanel";

// The status panel queries live dependencies, so this page is never prerendered.
export const dynamic = "force-dynamic";

/**
 * Stage 1 landing page.
 *
 * Deliberately minimal: it exists to prove the frontend and backend are wired
 * together. Repository browsing, chat, the RAG inspector and the agent trace
 * are added in later milestones.
 */
export default function Home() {
  return (
    <main className="mx-auto max-w-2xl px-6 py-16">
      <h1 className="text-2xl font-semibold">AI Software Engineering Platform</h1>
      <p className="mt-2 text-sm text-black/60 dark:text-white/60">
        Stage 1 — walking skeleton. Connect a repository, index it, and ask
        questions about the code.
      </p>

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
          <li>GitHub sign-in and repository selection (milestone 2)</li>
          <li>Repository indexing and progress (milestones 3–4)</li>
          <li>Hybrid retrieval, chat and the RAG inspector (milestones 5–8)</li>
          <li>Agent, sandbox and patch proposals (milestone 9)</li>
        </ul>
      </section>
    </main>
  );
}

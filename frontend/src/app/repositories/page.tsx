import Link from "next/link";

import { SignInButton } from "@/features/auth/SignInButton";
import { UserMenu } from "@/features/auth/UserMenu";
import type { User } from "@/features/auth/types";
import { IndexButton } from "@/features/repositories/IndexButton";
import { RepositoryActionButton } from "@/features/repositories/RepositoryActionButton";
import { ChatPanel } from "@/features/chat/ChatPanel";
import { SearchPanel } from "@/features/search/SearchPanel";
import {
  connectRepository,
  disconnectRepository,
} from "@/features/repositories/actions";
import type {
  ConnectedRepository,
  GitHubRepositoryPage,
} from "@/features/repositories/types";
import { ApiError } from "@/lib/api";
import { authedFetch, getCurrentUser } from "@/lib/session";

// Everything here is per-user and read live from GitHub, so nothing may be
// cached or prerendered.
export const dynamic = "force-dynamic";

interface PageProps {
  searchParams: Promise<{ page?: string }>;
}

export default async function RepositoriesPage({ searchParams }: PageProps) {
  const user = await getCurrentUser();
  if (!user) {
    return (
      <Shell>
        <h1 className="text-2xl font-semibold">Repositories</h1>
        <p className="mt-2 text-sm text-black/60 dark:text-white/60">
          Sign in with GitHub to choose the repositories this platform may read.
        </p>
        <div className="mt-6">
          <SignInButton />
        </div>
      </Shell>
    );
  }

  const page = parsePage((await searchParams).page);

  let connected: ConnectedRepository[];
  let available: GitHubRepositoryPage;
  try {
    [connected, available] = await Promise.all([
      authedFetch<ConnectedRepository[]>("/repositories"),
      authedFetch<GitHubRepositoryPage>(`/repositories/github?page=${page}&per_page=20`),
    ]);
  } catch (error) {
    return (
      <Shell user={user}>
        <h1 className="text-2xl font-semibold">Repositories</h1>
        <ErrorPanel
          message={
            error instanceof ApiError
              ? error.message
              : "Your repositories could not be loaded."
          }
        />
      </Shell>
    );
  }

  // Search needs vectors, so it is offered only where they exist. Showing a
  // search box that cannot return anything would be worse than showing none.
  const searchable = connected.find((repo) => repo.embedded_chunks > 0);

  return (
    <Shell user={user}>
      <h1 className="text-2xl font-semibold">Repositories</h1>

      <section className="mt-8">
        <h2 className="text-sm font-medium text-black/70 dark:text-white/70">
          Connected ({connected.length})
        </h2>
        {connected.length === 0 ? (
          <p className="mt-2 text-sm text-black/50 dark:text-white/50">
            None yet. Connect one below to make it available for indexing.
          </p>
        ) : (
          <ul className="mt-3 divide-y divide-black/5 rounded-lg border border-black/10 dark:divide-white/10 dark:border-white/15">
            {connected.map((repo) => (
              <li
                key={repo.id}
                className="flex items-center justify-between gap-4 px-4 py-3"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">
                    {repo.owner}/{repo.name}
                  </p>
                  <p className="mt-0.5 text-xs text-black/50 dark:text-white/50">
                    {repo.default_branch} · {formatStatus(repo.index_status)}
                    {repo.indexed_at && ` · ${formatWhen(repo.indexed_at)}`}
                  </p>
                  {repo.chunk_count > 0 && (
                    <p className="mt-0.5 text-xs text-black/40 dark:text-white/40">
                      {formatIndexSize(repo)}
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <IndexButton
                    repositoryId={repo.id}
                    indexStatus={repo.index_status}
                  />
                  <RepositoryActionButton
                    variant="secondary"
                    label="Disconnect"
                    pendingLabel="Disconnecting…"
                    action={disconnectRepository.bind(null, repo.id)}
                  />
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {searchable && <ChatPanel repositoryId={searchable.id} />}
      {searchable && <SearchPanel repositoryId={searchable.id} />}

      <section className="mt-10">
        <h2 className="text-sm font-medium text-black/70 dark:text-white/70">
          On GitHub
        </h2>
        {available.items.length === 0 ? (
          <p className="mt-2 text-sm text-black/50 dark:text-white/50">
            No repositories were returned for this page.
          </p>
        ) : (
          <ul className="mt-3 divide-y divide-black/5 rounded-lg border border-black/10 dark:divide-white/10 dark:border-white/15">
            {available.items.map((repo) => (
              <li
                key={repo.github_id}
                className="flex items-center justify-between gap-4 px-4 py-3"
              >
                <div className="min-w-0">
                  <p className="flex items-center gap-2 text-sm font-medium">
                    <span className="truncate">{repo.full_name}</span>
                    {repo.is_private && (
                      <span className="shrink-0 rounded-full bg-black/5 px-2 py-0.5 text-xs font-normal text-black/60 dark:bg-white/10 dark:text-white/60">
                        Private
                      </span>
                    )}
                  </p>
                  {repo.description && (
                    <p className="mt-0.5 truncate text-xs text-black/50 dark:text-white/50">
                      {repo.description}
                    </p>
                  )}
                  <p className="mt-0.5 text-xs text-black/40 dark:text-white/40">
                    {[repo.language, repo.default_branch].filter(Boolean).join(" · ")}
                  </p>
                </div>
                {repo.connected_id ? (
                  <span className="shrink-0 text-sm text-black/40 dark:text-white/40">
                    Connected
                  </span>
                ) : (
                  <RepositoryActionButton
                    label="Connect"
                    pendingLabel="Connecting…"
                    action={connectRepository.bind(null, repo.owner, repo.name)}
                  />
                )}
              </li>
            ))}
          </ul>
        )}

        <nav className="mt-4 flex items-center gap-4 text-sm">
          {page > 1 && (
            <Link href={`/repositories?page=${page - 1}`} className="underline">
              Previous
            </Link>
          )}
          {available.has_next && (
            <Link href={`/repositories?page=${page + 1}`} className="underline">
              Next
            </Link>
          )}
          <span className="text-black/40 dark:text-white/40">Page {page}</span>
        </nav>
      </section>
    </Shell>
  );
}

function Shell({
  user,
  children,
}: {
  user?: User;
  children: React.ReactNode;
}) {
  return (
    <main className="mx-auto max-w-2xl px-6 py-16">
      <div className="mb-8 flex items-center justify-between gap-4">
        <Link href="/" className="text-sm text-black/50 hover:underline dark:text-white/50">
          ← Home
        </Link>
        {user && <UserMenu user={user} />}
      </div>
      {children}
    </main>
  );
}

function ErrorPanel({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="mt-6 rounded-lg border border-red-500/40 bg-red-500/5 p-6 text-sm"
    >
      <p className="font-medium text-red-700 dark:text-red-400">
        Could not load repositories
      </p>
      <p className="mt-1 text-black/70 dark:text-white/70">{message}</p>
    </div>
  );
}

/** Clamp an untrusted query parameter to a usable page number. */
function parsePage(raw: string | undefined): number {
  const parsed = Number.parseInt(raw ?? "1", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 1;
}

function formatStatus(status: ConnectedRepository["index_status"]): string {
  return status === "not_indexed" ? "not indexed yet" : status.replace("_", " ");
}

/**
 * Describe the index by its real counts.
 *
 * A partial embedding pass is stated rather than rounded away: it is the
 * difference between a searchable index and one that silently misses results.
 */
function formatIndexSize(repo: ConnectedRepository): string {
  const base = `${repo.file_count} files · ${repo.chunk_count} chunks`;
  if (repo.embedded_chunks === repo.chunk_count) {
    return `${base} · all embedded`;
  }
  return `${base} · ${repo.embedded_chunks} embedded`;
}

/** Render a timestamp the server produced, without inventing precision. */
function formatWhen(iso: string): string {
  const when = new Date(iso);
  return Number.isNaN(when.getTime())
    ? ""
    : `indexed ${when.toLocaleDateString()} ${when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

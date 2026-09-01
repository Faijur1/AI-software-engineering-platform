"use client";

import { useState, useTransition } from "react";

import { search } from "@/features/search/actions";
import type { SearchHit, SearchResponse } from "@/features/search/types";

/**
 * Hybrid search over one repository.
 *
 * Every result shows how it was found and what each retriever scored it. That
 * is not debug output: a retrieval failure is only diagnosable if you can see
 * whether the vector side, the keyword side, or both put a chunk where it is.
 * The full inspector is milestone 8; this is the honest minimum.
 */
export function SearchPanel({ repositoryId }: { repositoryId: string }) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    startTransition(async () => {
      const outcome = await search(repositoryId, query);
      setError(outcome.error);
      setResult(outcome.response);
    });
  };

  return (
    <section className="mt-10">
      <h2 className="text-sm font-medium text-black/70 dark:text-white/70">
        Search this repository
      </h2>

      <form onSubmit={submit} className="mt-3 flex gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="e.g. where is the OAuth callback handled"
          aria-label="Search query"
          className="flex-1 rounded-md border border-black/15 bg-transparent px-3 py-2 text-sm outline-none focus:border-black/40 dark:border-white/20 dark:focus:border-white/50"
        />
        <button
          type="submit"
          disabled={pending || !query.trim()}
          className="rounded-md bg-black px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-85 disabled:opacity-40 dark:bg-white dark:text-black"
        >
          {pending ? "Searching…" : "Search"}
        </button>
      </form>

      {error && (
        <p role="alert" className="mt-3 text-sm text-red-700 dark:text-red-400">
          {error}
        </p>
      )}

      {result && <Results result={result} />}
    </section>
  );
}

function Results({ result }: { result: SearchResponse }) {
  if (result.results.length === 0) {
    return (
      <p className="mt-4 text-sm text-black/50 dark:text-white/50">
        No matches. Vector search returned {result.vector_candidates} candidates
        and keyword search {result.keyword_candidates}.
      </p>
    );
  }

  return (
    <div className="mt-4">
      <p className="text-xs text-black/50 dark:text-white/50">
        {result.vector_candidates} vector + {result.keyword_candidates} keyword
        candidates → {result.fused_candidates} after merge → showing{" "}
        {result.results.length}
        {result.reranker_is_passthrough && (
          <>
            {" "}
            ·{" "}
            <span className="text-amber-700 dark:text-amber-500">
              not reranked yet (milestone 7)
            </span>
          </>
        )}
      </p>

      {result.notes.map((note) => (
        <p
          key={note}
          className="mt-2 rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-800 dark:text-amber-300"
        >
          {note}
        </p>
      ))}

      <ul className="mt-3 space-y-3">
        {result.results.map((hit) => (
          <Hit key={hit.chunk_id} hit={hit} />
        ))}
      </ul>
    </div>
  );
}

const METHOD_STYLES: Record<string, string> = {
  both: "bg-green-500/15 text-green-700 dark:text-green-400",
  vector: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
  keyword: "bg-purple-500/15 text-purple-700 dark:text-purple-400",
};

function Hit({ hit }: { hit: SearchHit }) {
  return (
    <li className="rounded-lg border border-black/10 p-3 dark:border-white/15">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-mono text-xs text-black/70 dark:text-white/70">
            {hit.file_path}:{hit.start_line}-{hit.end_line}
          </p>
          {hit.symbol && (
            <p className="mt-0.5 truncate text-sm font-medium">{hit.symbol}</p>
          )}
        </div>
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${METHOD_STYLES[hit.method]}`}
        >
          {hit.method}
        </span>
      </div>

      <pre className="mt-2 max-h-40 overflow-auto rounded bg-black/5 p-2 text-xs dark:bg-white/10">
        <code>{hit.content.slice(0, 600)}</code>
      </pre>

      {/* Each retriever's own score, so a ranking can be explained rather than
          just asserted. Absent scores render as "—", never as zero. */}
      <p className="mt-2 font-mono text-xs text-black/40 dark:text-white/40">
        fused {hit.fused_score.toFixed(4)} · vector{" "}
        {format(hit.vector_score, hit.vector_rank)} · keyword{" "}
        {format(hit.keyword_score, hit.keyword_rank)}
      </p>
    </li>
  );
}

function format(score: number | null, rank: number | null): string {
  if (score === null) return "—";
  return `${score.toFixed(4)} (#${rank})`;
}

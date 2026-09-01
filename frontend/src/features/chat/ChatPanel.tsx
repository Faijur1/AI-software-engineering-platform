"use client";

import { useState, useTransition } from "react";

import { ask } from "@/features/chat/actions";
import type { ChatResponse, CitedSource } from "@/features/chat/types";

/**
 * Ask a question about the indexed repository.
 *
 * The answer is shown next to the exact sources it was generated from, and
 * citations are rendered as links into those sources. That pairing is the
 * point: an answer about code the reader can check is only useful if checking
 * it is easy, and an uncheckable citation is decoration.
 */
export function ChatPanel({ repositoryId }: { repositoryId: string }) {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<ChatResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    startTransition(async () => {
      const outcome = await ask(repositoryId, question);
      setError(outcome.error);
      setResult(outcome.response);
    });
  };

  return (
    <section className="mt-10">
      <h2 className="text-sm font-medium text-black/70 dark:text-white/70">
        Ask about this repository
      </h2>

      <form onSubmit={submit} className="mt-3 flex gap-2">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. how are GitHub tokens encrypted before storage?"
          aria-label="Question"
          className="flex-1 rounded-md border border-black/15 bg-transparent px-3 py-2 text-sm outline-none focus:border-black/40 dark:border-white/20 dark:focus:border-white/50"
        />
        <button
          type="submit"
          disabled={pending || !question.trim()}
          className="rounded-md bg-black px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-85 disabled:opacity-40 dark:bg-white dark:text-black"
        >
          {pending ? "Thinking…" : "Ask"}
        </button>
      </form>

      {pending && (
        <p className="mt-3 text-xs text-black/50 dark:text-white/50">
          Retrieving, then generating locally. This usually takes 10–60 seconds.
        </p>
      )}

      {error && (
        <p role="alert" className="mt-3 text-sm text-red-700 dark:text-red-400">
          {error}
        </p>
      )}

      {result && !pending && <Answer result={result} />}
    </section>
  );
}

function Answer({ result }: { result: ChatResponse }) {
  const cited = result.sources.filter((s) => s.cited);

  return (
    <div className="mt-4">
      {/* An answer citing evidence that does not exist is the failure the
          reader most needs to know about, so it leads rather than hides. */}
      {!result.citations.valid && (
        <p
          role="alert"
          className="mb-3 rounded-md border border-red-500/40 bg-red-500/5 px-3 py-2 text-xs text-red-800 dark:text-red-300"
        >
          This answer cited sources that do not exist (
          {result.citations.invalid_indices.join(", ")}). Treat it with
          suspicion.
        </p>
      )}

      {result.notes.map((note) => (
        <p
          key={note}
          className="mb-2 rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-800 dark:text-amber-300"
        >
          {note}
        </p>
      ))}

      <div className="rounded-lg border border-black/10 p-4 dark:border-white/15">
        <p className="whitespace-pre-wrap text-sm leading-relaxed">
          {result.answer}
        </p>
      </div>

      <p className="mt-2 font-mono text-xs text-black/40 dark:text-white/40">
        {result.model} · {result.sources_included} of {result.sources_offered}{" "}
        sources used · ~{result.estimated_context_tokens} context tokens ·{" "}
        {(result.duration_ms / 1000).toFixed(1)}s ·{" "}
        {Math.round(result.citations.citation_coverage * 100)}% of sentences cited
      </p>

      <h3 className="mt-5 text-xs font-medium text-black/60 dark:text-white/60">
        Sources ({cited.length} of {result.sources.length} cited)
      </h3>
      <ul className="mt-2 space-y-2">
        {result.sources.map((source) => (
          <SourceCard key={source.chunk_id} source={source} />
        ))}
      </ul>
    </div>
  );
}

function SourceCard({ source }: { source: CitedSource }) {
  return (
    <li
      className={`rounded-lg border p-3 ${
        source.cited
          ? "border-black/20 dark:border-white/25"
          : "border-black/10 opacity-55 dark:border-white/10"
      }`}
    >
      <div className="flex items-baseline justify-between gap-3">
        <p className="truncate font-mono text-xs">
          <span className="mr-2 font-semibold">[{source.index}]</span>
          {source.file_path}:{source.start_line}-{source.end_line}
          {source.symbol && (
            <span className="ml-2 text-black/50 dark:text-white/50">
              {source.symbol}
            </span>
          )}
        </p>
        {!source.cited && (
          <span className="shrink-0 text-xs text-black/40 dark:text-white/40">
            not cited
          </span>
        )}
      </div>
      <pre className="mt-2 max-h-36 overflow-auto rounded bg-black/5 p-2 text-xs dark:bg-white/10">
        <code>{source.content.slice(0, 700)}</code>
      </pre>
    </li>
  );
}

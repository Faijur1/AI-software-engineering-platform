"use client";

import { useMemo, useState, useTransition } from "react";

import { search } from "@/features/search/actions";
import type { SearchHit, SearchResponse } from "@/features/search/types";

/**
 * The RAG inspector.
 *
 * Shows **every** candidate the retrievers produced, not just the ones that
 * survived. That distinction is the whole point: seeing the chosen chunks
 * cannot answer "why was the right one not chosen", which is the question a
 * retrieval failure actually poses.
 *
 * Every number displayed is one the backend computed. Nothing here re-ranks,
 * re-scores, or infers — the UI would then be explaining itself rather than
 * the pipeline.
 */
export function InspectorPanel({ repositoryId }: { repositoryId: string }) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const [showOnlySelected, setShowOnlySelected] = useState(false);
  const [methodFilter, setMethodFilter] = useState<string>("all");
  const [roleFilter, setRoleFilter] = useState<string>("all");
  const [expanded, setExpanded] = useState<string | null>(null);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setExpanded(null);
    startTransition(async () => {
      const outcome = await search(repositoryId, query, true);
      setError(outcome.error);
      setResult(outcome.response);
    });
  };

  // Memoised so the `?? []` fallback does not produce a new array identity on
  // every render and invalidate the filters below.
  const candidates = useMemo(() => result?.candidates ?? [], [result]);

  const visible = useMemo(
    () =>
      candidates.filter(
        (c) =>
          (!showOnlySelected || c.selected) &&
          (methodFilter === "all" || c.method === methodFilter) &&
          (roleFilter === "all" || c.role === roleFilter),
      ),
    [candidates, showOnlySelected, methodFilter, roleFilter],
  );

  const roles = useMemo(
    () => Array.from(new Set(candidates.map((c) => c.role ?? "?"))).sort(),
    [candidates],
  );

  return (
    <section className="mt-10">
      <h2 className="text-sm font-medium text-black/70 dark:text-white/70">
        Retrieval inspector
      </h2>
      <p className="mt-1 text-xs text-black/50 dark:text-white/50">
        Every candidate both retrievers produced, with the scores behind its
        rank. Use it to see why an answer was wrong, not just that it was.
      </p>

      <form onSubmit={submit} className="mt-3 flex gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="e.g. what stops secret files from being indexed"
          aria-label="Inspector query"
          className="flex-1 rounded-md border border-black/15 bg-transparent px-3 py-2 text-sm outline-none focus:border-black/40 dark:border-white/20 dark:focus:border-white/50"
        />
        <button
          type="submit"
          disabled={pending || !query.trim()}
          className="rounded-md bg-black px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-85 disabled:opacity-40 dark:bg-white dark:text-black"
        >
          {pending ? "Retrieving…" : "Inspect"}
        </button>
      </form>

      {error && (
        <p role="alert" className="mt-3 text-sm text-red-700 dark:text-red-400">
          {error}
        </p>
      )}

      {result && !pending && (
        <>
          <Summary result={result} />

          <div className="mt-3 flex flex-wrap items-center gap-3 text-xs">
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={showOnlySelected}
                onChange={(e) => setShowOnlySelected(e.target.checked)}
              />
              Selected only
            </label>
            <Select
              label="Method"
              value={methodFilter}
              onChange={setMethodFilter}
              options={["all", "both", "vector", "keyword"]}
            />
            <Select
              label="Role"
              value={roleFilter}
              onChange={setRoleFilter}
              options={["all", ...roles]}
            />
            <span className="text-black/40 dark:text-white/40">
              {visible.length} of {candidates.length} shown
            </span>
          </div>

          <CandidateTable
            candidates={visible}
            allCandidates={candidates}
            expanded={expanded}
            onToggle={(id) => setExpanded(expanded === id ? null : id)}
          />
        </>
      )}
    </section>
  );
}

function Summary({ result }: { result: SearchResponse }) {
  return (
    <div className="mt-4 rounded-lg border border-black/10 p-3 text-xs dark:border-white/15">
      <p className="font-mono">
        vector {result.vector_candidates} + keyword {result.keyword_candidates} →{" "}
        {result.fused_candidates} fused → {result.results.length} selected
      </p>
      <p className="mt-1 text-black/50 dark:text-white/50">
        reranker: <span className="font-mono">{result.reranker}</span>
        {result.reranker_is_passthrough && (
          <span className="ml-2 text-amber-700 dark:text-amber-500">
            passthrough — ranks are fusion order only
          </span>
        )}
      </p>
      {result.notes.map((note) => (
        <p key={note} className="mt-1 text-amber-700 dark:text-amber-500">
          {note}
        </p>
      ))}
    </div>
  );
}

const METHOD_STYLES: Record<string, string> = {
  both: "bg-green-500/15 text-green-700 dark:text-green-400",
  vector: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
  keyword: "bg-purple-500/15 text-purple-700 dark:text-purple-400",
};

function CandidateTable({
  candidates,
  allCandidates,
  expanded,
  onToggle,
}: {
  candidates: SearchHit[];
  allCandidates: SearchHit[];
  expanded: string | null;
  onToggle: (id: string) => void;
}) {
  if (candidates.length === 0) {
    return (
      <p className="mt-4 text-sm text-black/50 dark:text-white/50">
        No candidates match these filters.
      </p>
    );
  }

  return (
    // Wide table, so it scrolls inside its own container rather than making
    // the page scroll sideways.
    <div className="mt-3 overflow-x-auto rounded-lg border border-black/10 dark:border-white/15">
      <table className="w-full min-w-[820px] text-left text-xs">
        <thead className="border-b border-black/10 bg-black/[0.03] dark:border-white/15 dark:bg-white/[0.04]">
          <tr>
            <Th className="w-10">#</Th>
            <Th className="w-8" title="Selected into the answer context">
              ✓
            </Th>
            <Th>Location</Th>
            <Th className="w-20">Method</Th>
            <Th className="w-16">Role</Th>
            <Th className="w-24 text-right">Rerank</Th>
            <Th className="w-24 text-right">Fused</Th>
            <Th className="w-28 text-right">Vector</Th>
            <Th className="w-28 text-right">Keyword</Th>
          </tr>
        </thead>
        <tbody>
          {candidates.map((c) => {
            // Rank within the full ordered list, so filtering does not
            // renumber rows and quietly misrepresent position.
            const rank = allCandidates.indexOf(c) + 1;
            const open = expanded === c.chunk_id;
            return (
              <Row
                key={c.chunk_id}
                candidate={c}
                rank={rank}
                open={open}
                onToggle={() => onToggle(c.chunk_id)}
              />
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Row({
  candidate: c,
  rank,
  open,
  onToggle,
}: {
  candidate: SearchHit;
  rank: number;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr
        onClick={onToggle}
        className={`cursor-pointer border-b border-black/5 hover:bg-black/[0.03] dark:border-white/10 dark:hover:bg-white/[0.05] ${
          c.selected ? "" : "opacity-70"
        }`}
      >
        <Td className="tabular-nums text-black/40 dark:text-white/40">{rank}</Td>
        <Td>{c.selected ? "●" : ""}</Td>
        <Td>
          <span className="font-mono">
            {c.file_path}:{c.start_line}-{c.end_line}
          </span>
          {c.symbol && (
            <span className="ml-2 text-black/50 dark:text-white/50">{c.symbol}</span>
          )}
        </Td>
        <Td>
          <span
            className={`rounded-full px-1.5 py-0.5 ${METHOD_STYLES[c.method] ?? ""}`}
          >
            {c.method}
          </span>
        </Td>
        <Td className="text-black/50 dark:text-white/50">{c.role ?? "—"}</Td>
        <Td className="text-right font-mono tabular-nums">
          {fmt(c.rerank_score, 5)}
        </Td>
        <Td className="text-right font-mono tabular-nums">
          {c.fused_score.toFixed(5)}
        </Td>
        <Td className="text-right font-mono tabular-nums">
          {pair(c.vector_score, c.vector_rank, 3)}
        </Td>
        <Td className="text-right font-mono tabular-nums">
          {pair(c.keyword_score, c.keyword_rank, 4)}
        </Td>
      </tr>
      {open && (
        <tr className="border-b border-black/5 dark:border-white/10">
          <td colSpan={9} className="p-0">
            <pre className="max-h-72 overflow-auto bg-black/5 p-3 text-xs dark:bg-white/10">
              <code>{c.content}</code>
            </pre>
          </td>
        </tr>
      )}
    </>
  );
}

function Th({
  children,
  className = "",
  title,
}: {
  children: React.ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <th
      title={title}
      className={`px-2 py-2 font-medium text-black/60 dark:text-white/60 ${className}`}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <td className={`px-2 py-1.5 ${className}`}>{children}</td>;
}

function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: string[];
}) {
  return (
    <label className="flex items-center gap-1.5">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded border border-black/15 bg-transparent px-1.5 py-0.5 dark:border-white/20"
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  );
}

/** An absent score renders as an em dash, never as zero. */
function fmt(value: number | null, digits: number): string {
  return value === null ? "—" : value.toFixed(digits);
}

function pair(score: number | null, rank: number | null, digits: number): string {
  if (score === null) return "—";
  return `${score.toFixed(digits)} #${rank}`;
}

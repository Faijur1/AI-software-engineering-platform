"use client";

import { useEffect, useState } from "react";

import { readRun, readTrace, startRun } from "@/features/agent/actions";
import type { AgentRunDetail, ToolRun, Trace, TraceEvent } from "@/features/agent/types";

// A run is minutes of model calls. Three seconds is often enough to see the
// next iteration without hammering the API.
const POLL_INTERVAL_MS = 3000;

const TERMINAL = new Set(["succeeded", "failed", "max_iterations_exceeded"]);

/**
 * Start an agent run and watch what it actually did.
 *
 * The trace and the tool calls are the point, not just the answer. A run that
 * reached the iteration cap, or that spent three iterations on refused calls,
 * is far more informative than its final text — and with a small local model
 * that is the common case.
 */
export function AgentPanel({ repositoryId }: { repositoryId: string }) {
  const [task, setTask] = useState("");
  const [allowTests, setAllowTests] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<AgentRunDetail | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    if (runId === null) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async () => {
      const latest = await readRun(runId);
      if (cancelled) return;

      if (latest === null) {
        setError("Lost track of this run. Reload to see its state.");
        setRunId(null);
        return;
      }

      setRun(latest);
      if (TERMINAL.has(latest.status)) {
        setTrace(await readTrace(latest.trace_id));
        setRunId(null);
        return;
      }
      timer = setTimeout(() => void tick(), POLL_INTERVAL_MS);
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [runId]);

  const begin = async () => {
    setStarting(true);
    setError(null);
    setTrace(null);
    setRun(null);
    const result = await startRun(repositoryId, task, allowTests);
    setStarting(false);

    if (result.error !== null || result.run === null) {
      setError(result.error ?? "The run could not be started.");
      return;
    }
    setRun({ ...result.run, tool_runs: [], patch_ids: [] });
    setRunId(result.run.id);
  };

  const active = starting || runId !== null;

  return (
    <section className="mt-10">
      <h2 className="text-sm font-medium text-black/70 dark:text-white/70">
        Agent
      </h2>
      <p className="mt-1 text-xs text-black/50 dark:text-white/50">
        Give it a task and watch what it does. The trace shows every tool call,
        including the ones that were refused.
      </p>

      <div className="mt-3 flex gap-2">
        <input
          value={task}
          onChange={(e) => setTask(e.target.value)}
          placeholder="e.g. which function verifies the OAuth state parameter?"
          aria-label="Agent task"
          disabled={active}
          className="flex-1 rounded-md border border-black/15 bg-transparent px-3 py-2 text-sm outline-none focus:border-black/40 disabled:opacity-50 dark:border-white/20 dark:focus:border-white/50"
        />
        <button
          type="button"
          onClick={() => void begin()}
          disabled={active || !task.trim()}
          className="rounded-md bg-black px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-85 disabled:opacity-40 dark:bg-white dark:text-black"
        >
          {active ? "Running…" : "Run"}
        </button>
      </div>

      <label className="mt-2 flex items-center gap-1.5 text-xs text-black/60 dark:text-white/60">
        <input
          type="checkbox"
          checked={allowTests}
          onChange={(e) => setAllowTests(e.target.checked)}
          disabled={active}
        />
        Allow running the test suite in the sandbox
        <span className="text-black/40 dark:text-white/40">
          (a much larger capability than reading)
        </span>
      </label>

      {error && (
        <p role="alert" className="mt-3 text-sm text-red-700 dark:text-red-400">
          {error}
        </p>
      )}

      {run && <RunView run={run} trace={trace} />}
    </section>
  );
}

const STATUS_STYLES: Record<string, string> = {
  queued: "bg-black/10 text-black/70 dark:bg-white/15 dark:text-white/70",
  running: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
  succeeded: "bg-green-500/15 text-green-700 dark:text-green-400",
  failed: "bg-red-500/15 text-red-700 dark:text-red-400",
  max_iterations_exceeded: "bg-amber-500/15 text-amber-700 dark:text-amber-500",
};

function RunView({ run, trace }: { run: AgentRunDetail; trace: Trace | null }) {
  return (
    <div className="mt-4 rounded-lg border border-black/10 p-4 dark:border-white/15">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span
          className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_STYLES[run.status] ?? ""}`}
        >
          {run.status.replace(/_/g, " ")}
        </span>
        <span className="font-mono text-xs text-black/40 dark:text-white/40">
          iteration {run.iterations}/{run.max_iterations}
          {run.model && ` · ${run.model}`}
        </span>
      </div>

      {/* Reaching the cap is a distinct outcome, not a failure, and the
          distinction is worth surfacing rather than flattening. */}
      {run.status === "max_iterations_exceeded" && (
        <p className="mt-3 rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
          The run hit its iteration limit without reaching an answer. What it
          did find is below — it did not guess a conclusion from an unfinished
          investigation.
        </p>
      )}

      {run.error && run.status === "failed" && (
        <p role="alert" className="mt-3 text-xs text-red-700 dark:text-red-400">
          {run.error}
        </p>
      )}

      {run.result && (
        <div className="mt-3">
          <h3 className="text-xs font-medium text-black/60 dark:text-white/60">
            Answer
          </h3>
          <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed">
            {run.result}
          </p>
        </div>
      )}

      {run.tool_runs.length > 0 && <ToolCalls calls={run.tool_runs} />}
      {trace && <TraceView trace={trace} />}
    </div>
  );
}

const TOOL_STATUS_STYLES: Record<string, string> = {
  succeeded: "text-green-700 dark:text-green-400",
  failed: "text-red-700 dark:text-red-400",
  rejected: "text-amber-700 dark:text-amber-500",
};

function ToolCalls({ calls }: { calls: ToolRun[] }) {
  return (
    <div className="mt-4">
      <h3 className="text-xs font-medium text-black/60 dark:text-white/60">
        Tool calls ({calls.length})
      </h3>
      <ul className="mt-2 space-y-1.5">
        {calls.map((call) => (
          <li
            key={call.id}
            className="rounded border border-black/10 px-2.5 py-1.5 font-mono text-xs dark:border-white/10"
          >
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="text-black/40 dark:text-white/40">
                it{call.iteration}
              </span>
              <span className="font-semibold">{call.tool_name}</span>
              <span className={TOOL_STATUS_STYLES[call.status] ?? ""}>
                {call.status}
              </span>
              <span className="text-black/40 dark:text-white/40">
                {call.duration_ms}ms
              </span>
            </div>
            <p className="mt-0.5 truncate text-black/50 dark:text-white/50">
              {JSON.stringify(call.input)}
            </p>
            {/* A refusal is the interesting case: it is what the guardrails
                did, and what the model was told in response. */}
            {call.error && (
              <p className="mt-0.5 text-amber-700 dark:text-amber-500">
                {call.error}
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function TraceView({ trace }: { trace: Trace }) {
  return (
    <div className="mt-4">
      <h3 className="text-xs font-medium text-black/60 dark:text-white/60">
        Trace ({trace.events.length} events)
      </h3>
      <ol className="mt-2 space-y-0.5">
        {trace.events.map((event) => (
          <TraceRow key={event.sequence} event={event} />
        ))}
      </ol>
    </div>
  );
}

function TraceRow({ event }: { event: TraceEvent }) {
  const detail =
    (event.event_metadata.tool as string | undefined) ??
    (event.event_metadata.reason as string | undefined) ??
    "";

  return (
    <li className="flex items-baseline gap-2 font-mono text-xs">
      <span className="w-6 shrink-0 text-right text-black/30 dark:text-white/30">
        {event.sequence}
      </span>
      <span className="w-52 shrink-0 truncate">{event.event_type}</span>
      <span className="w-16 shrink-0 text-black/40 dark:text-white/40">
        {event.duration_ms !== null ? `${event.duration_ms}ms` : ""}
      </span>
      <span className="truncate text-black/50 dark:text-white/50">{detail}</span>
    </li>
  );
}

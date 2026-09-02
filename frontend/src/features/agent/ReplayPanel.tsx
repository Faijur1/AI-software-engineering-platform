"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { listRuns, readTrace } from "@/features/agent/actions";
import type { AgentRun, Trace, TraceEvent } from "@/features/agent/types";

/**
 * Replay a recorded agent run.
 *
 * **This is a view over stored events, never a reconstruction or an
 * animation** (docs/agents.md). Every event shown was written to the database
 * as it happened, and the gaps between them are the real recorded intervals —
 * scaled by the speed control, never invented. A replay that made up plausible
 * timing would be a dramatisation of a run rather than a record of one, and
 * would be useless for the thing replay exists for: seeing where a run
 * actually spent its time.
 *
 * Consequence worth knowing: a run whose model calls took 30 seconds each
 * really does replay slowly at 1x. That is the point. The speed control exists
 * so the honest timing can be compressed without being falsified.
 */
const SPEEDS = [1, 2, 5, 20] as const;
// Long gaps are clamped so one slow model call does not stall a replay
// entirely. The clamp is disclosed in the UI rather than applied silently.
const MAX_STEP_MS = 4000;

export function ReplayPanel() {
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [loading, setLoading] = useState(false);

  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<number>(2);

  useEffect(() => {
    void (async () => setRuns(await listRuns()))();
  }, []);

  const load = useCallback(async (run: AgentRun) => {
    setLoading(true);
    setSelected(run.id);
    setPlaying(false);
    setCursor(0);
    setTrace(await readTrace(run.trace_id));
    setLoading(false);
  }, []);

  const events = useMemo(() => trace?.events ?? [], [trace]);

  // Real recorded gaps between consecutive events, in milliseconds.
  const gaps = useMemo(() => {
    return events.map((event, index) => {
      if (index === 0) return 0;
      const previous = new Date(events[index - 1].ts).getTime();
      const current = new Date(event.ts).getTime();
      return Math.max(0, current - previous);
    });
  }, [events]);

  useEffect(() => {
    if (!playing) return;
    const next = cursor + 1;
    // Nothing left to schedule; stopping happens in the callback below rather
    // than here, so this effect never sets state during render.
    if (next >= events.length) return;

    const delay = Math.min((gaps[next] ?? 0) / speed, MAX_STEP_MS);
    const timer = setTimeout(() => {
      setCursor(next);
      if (next >= events.length - 1) setPlaying(false);
    }, delay);
    return () => clearTimeout(timer);
  }, [playing, cursor, events.length, gaps, speed]);

  const total = events.length;
  const elapsedMs = useMemo(
    () => gaps.slice(0, cursor + 1).reduce((sum, gap) => sum + gap, 0),
    [gaps, cursor],
  );
  const totalMs = useMemo(() => gaps.reduce((sum, gap) => sum + gap, 0), [gaps]);

  return (
    <section className="mt-10">
      <h2 className="text-sm font-medium text-black/70 dark:text-white/70">
        Replay a run
      </h2>
      <p className="mt-1 text-xs text-black/50 dark:text-white/50">
        A view over recorded events. Timings are the intervals actually
        measured, scaled by the speed control — nothing here is simulated.
      </p>

      <div className="mt-3 flex flex-wrap gap-2">
        {runs.length === 0 ? (
          <p className="text-sm text-black/50 dark:text-white/50">
            No agent runs recorded yet.
          </p>
        ) : (
          runs.slice(0, 8).map((run) => (
            <button
              key={run.id}
              type="button"
              onClick={() => void load(run)}
              className={`max-w-xs truncate rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors ${
                selected === run.id
                  ? "border-black/40 bg-black/5 dark:border-white/50 dark:bg-white/10"
                  : "border-black/15 hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10"
              }`}
              title={run.task}
            >
              <span className="block truncate">{run.task}</span>
              <span className="text-black/40 dark:text-white/40">
                {run.status.replace(/_/g, " ")} · {run.iterations} iter
              </span>
            </button>
          ))
        )}
      </div>

      {loading && (
        <p className="mt-3 text-sm text-black/50 dark:text-white/50">Loading trace…</p>
      )}

      {trace && total > 0 && !loading && (
        <div className="mt-4 rounded-lg border border-black/10 p-4 dark:border-white/15">
          <Controls
            cursor={cursor}
            total={total}
            playing={playing}
            speed={speed}
            elapsedMs={elapsedMs}
            totalMs={totalMs}
            onPlayPause={() => setPlaying((p) => !p)}
            onStep={(delta) =>
              setCursor((c) => Math.min(total - 1, Math.max(0, c + delta)))
            }
            onScrub={(value) => {
              setPlaying(false);
              setCursor(value);
            }}
            onSpeed={setSpeed}
          />
          <EventList events={events} gaps={gaps} cursor={cursor} />
        </div>
      )}

      {trace && total === 0 && !loading && (
        <p className="mt-3 text-sm text-black/50 dark:text-white/50">
          This run recorded no events.
        </p>
      )}
    </section>
  );
}

function Controls({
  cursor,
  total,
  playing,
  speed,
  elapsedMs,
  totalMs,
  onPlayPause,
  onStep,
  onScrub,
  onSpeed,
}: {
  cursor: number;
  total: number;
  playing: boolean;
  speed: number;
  elapsedMs: number;
  totalMs: number;
  onPlayPause: () => void;
  onStep: (delta: number) => void;
  onScrub: (value: number) => void;
  onSpeed: (value: number) => void;
}) {
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => onStep(-1)}
          disabled={cursor === 0}
          aria-label="Step back"
          className="rounded-md border border-black/15 px-2.5 py-1 text-sm disabled:opacity-30 dark:border-white/20"
        >
          ‹
        </button>
        <button
          type="button"
          onClick={onPlayPause}
          disabled={cursor >= total - 1 && !playing}
          className="rounded-md bg-black px-3 py-1 text-sm font-medium text-white disabled:opacity-40 dark:bg-white dark:text-black"
        >
          {playing ? "Pause" : "Play"}
        </button>
        <button
          type="button"
          onClick={() => onStep(1)}
          disabled={cursor >= total - 1}
          aria-label="Step forward"
          className="rounded-md border border-black/15 px-2.5 py-1 text-sm disabled:opacity-30 dark:border-white/20"
        >
          ›
        </button>

        <div className="ml-2 flex items-center gap-1">
          {SPEEDS.map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => onSpeed(option)}
              className={`rounded px-1.5 py-0.5 text-xs ${
                speed === option
                  ? "bg-black text-white dark:bg-white dark:text-black"
                  : "border border-black/15 dark:border-white/20"
              }`}
            >
              {option}×
            </button>
          ))}
        </div>

        <span className="ml-auto font-mono text-xs text-black/50 dark:text-white/50">
          {cursor + 1}/{total} · {(elapsedMs / 1000).toFixed(1)}s of{" "}
          {(totalMs / 1000).toFixed(1)}s
        </span>
      </div>

      <input
        type="range"
        min={0}
        max={total - 1}
        value={cursor}
        onChange={(e) => onScrub(Number(e.target.value))}
        aria-label="Position in trace"
        className="mt-3 w-full"
      />
      <p className="mt-1 text-xs text-black/40 dark:text-white/40">
        Gaps longer than {MAX_STEP_MS / 1000}s are shortened during playback so
        one slow model call does not stall the replay. The elapsed figures above
        are the real recorded times.
      </p>
    </>
  );
}

function EventList({
  events,
  gaps,
  cursor,
}: {
  events: TraceEvent[];
  gaps: number[];
  cursor: number;
}) {
  return (
    <ol className="mt-4 space-y-0.5">
      {events.map((event, index) => {
        const reached = index <= cursor;
        const current = index === cursor;
        const detail =
          (event.event_metadata.tool as string | undefined) ??
          (event.event_metadata.reason as string | undefined) ??
          (event.event_metadata.task as string | undefined) ??
          "";
        return (
          <li
            key={event.sequence}
            className={`flex items-baseline gap-2 rounded px-1.5 py-0.5 font-mono text-xs transition-opacity ${
              current ? "bg-black/[0.06] dark:bg-white/[0.12]" : ""
            } ${reached ? "" : "opacity-30"}`}
          >
            <span className="w-6 shrink-0 text-right text-black/30 dark:text-white/30">
              {event.sequence}
            </span>
            <span className="w-14 shrink-0 text-right text-black/40 dark:text-white/40">
              {index === 0 ? "0.0s" : `+${(gaps[index] / 1000).toFixed(1)}s`}
            </span>
            <span className="w-52 shrink-0 truncate">{event.event_type}</span>
            <span className="w-16 shrink-0 text-black/40 dark:text-white/40">
              {event.status ?? ""}
            </span>
            <span className="truncate text-black/50 dark:text-white/50">
              {detail}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

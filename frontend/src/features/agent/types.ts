export type AgentStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  /** Distinct from failure: the run did work and has partial state to show. */
  | "max_iterations_exceeded";

export type ToolStatus = "succeeded" | "failed" | "rejected";

export interface ToolRun {
  id: string;
  iteration: number;
  tool_name: string;
  status: ToolStatus;
  duration_ms: number;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  error: string | null;
  created_at: string;
}

export interface AgentRun {
  id: string;
  trace_id: string;
  repository_id: string;
  task: string;
  status: AgentStatus;
  plan: string | null;
  result: string | null;
  iterations: number;
  max_iterations: number;
  model: string | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  created_at: string;
}

export interface AgentRunDetail extends AgentRun {
  tool_runs: ToolRun[];
  patch_ids: string[];
}

export interface TraceEvent {
  sequence: number;
  event_type: string;
  component: string;
  ts: string;
  duration_ms: number | null;
  status: string | null;
  event_metadata: Record<string, unknown>;
}

export interface Trace {
  trace_id: string;
  events: TraceEvent[];
}

export interface Patch {
  id: string;
  agent_run_id: string;
  diff: string;
  summary: string | null;
  status: "proposed" | "approved" | "rejected";
  /** Null means not validated — never "passed". */
  validated: boolean | null;
  validation_output: string | null;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
}

# Agent architecture

> **Status: planned (milestone 9).** Not implemented. Stage 1 is a single agent
> loop; specialised agents are deferred to Stage 2 (ADR-007).

## Stage 1 loop

```
task -> plan -> select action -> execute tool -> observe
     -> update state -> validate -> continue or finish
```

Bounded by a hard maximum iteration count. On reaching it the run terminates
with status `max_iterations_exceeded` and returns its partial state — it does
not silently return a confident-looking answer built from an unfinished
investigation.

## AgentState

`run_id`, `trace_id`, `task`, `repository_id`, `plan`, `messages`,
`retrieved_context`, `tool_results`, `test_results`, `patch`, `iteration`,
`status`.

State is persisted per iteration, so a run is inspectable while in flight and
reconstructible after a crash.

## Tools

Each tool declares a name, description, input schema, output schema, and
required permissions, and validates its input before executing.

| Tool | Purpose | Permission |
| --- | --- | --- |
| `search_code` | Hybrid retrieval over the indexed repository | repo:read |
| `read_file` | Read a file, or a line range | repo:read |
| `search_symbol` | Locate a function/class definition | repo:read |
| `get_repo_structure` | Directory tree overview | repo:read |
| `run_tests` | Execute the test suite in the sandbox | sandbox:execute |

Constraints that hold regardless of what the model asks for:

- Tool names are resolved against a fixed registry — the agent cannot invoke
  anything not registered.
- Permissions are checked in code before execution, never by prompt wording.
- Path arguments are resolved and confirmed to stay inside the repository
  workspace, so `../../etc/passwd` fails at validation.
- No Stage 1 tool writes to GitHub or the host filesystem.
- Every call is recorded in `tool_runs` with input, output, status and duration.

## Patches

The agent never edits code in place. It produces a unified diff, which is
applied only inside the sandbox for validation:

```
agent -> proposed patch -> diff -> sandbox -> tests -> human approval
```

Approval is a human action. Stage 1 stops at an approved patch record; Stage 2
adds branch and PR creation behind that same gate.

## Tracing

Every run emits ordered events to the `events` table under one `trace_id`:
`agent.started`, `rag.started`, `retrieval.completed`, `tool.started`,
`tool.completed`, `test.started`, `test.completed`, `patch.created`,
`agent.completed`.

Stage 1 exposes these as a list. The Stage 2 replay UI (play/pause/step/speed)
replays these stored events — it is a view over recorded data, never a
reconstruction or an animation.

## Evaluation

Agent metrics, measured not asserted: task success rate, tool selection
accuracy, iteration count, failure rate, test pass rate, execution latency.

These form the Stage 1 baseline. Stage 2's multi-agent split is justified only
if it beats that baseline (ADR-007).

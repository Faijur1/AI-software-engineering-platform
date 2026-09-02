# Agent architecture

> **Status: built (milestone 9), and limited by the local model.** The loop,
> tools, sandbox, patches and tracing all work and are tested. The model's
> *decisions* are poor — see [`docs/README.md`](README.md). Specialised agents
> remain deferred to Stage 2 (ADR-007).

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

Constraints that hold regardless of what the model asks for. All of these are
enforced in `app/agent/tools.py` and asserted by tests that ask the registry to
do the forbidden thing:

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

## Handling a weak model

The loop is built for the model it actually has. Two behaviours follow:

**A malformed proposal is control flow, not a crash.** Small models wrap JSON
in prose, fence it, or emit an object with neither a tool nor an answer. The
parser extracts the first balanced object — tracking string state so braces
inside strings do not break it — and where it cannot, reports a parse error
rather than inferring an action the model never asked for. Guessing intent
would be worse than failing. The iteration is spent and the loop continues,
because failing the run outright would make the agent unusable with any model
that is not already excellent.

**A refusal is information.** A rejected tool call is fed back in words the
model can act on, and recorded rather than discarded. In a live run the model
asked to read a file with no workspace mounted, was refused, and chose
`search_symbol` instead — the model chooses, the code decides.

## Evaluation

Agent metrics, measured not asserted: task success rate, tool selection
accuracy, iteration count, failure rate, test pass rate, execution latency.

    python -m eval.agent_cli --workspace <checkout>

12 labelled tasks about this codebase, each with the file and symbol that
answers it, split between two shapes: `lookup`, where one search should
suffice, and `investigation`, where several tool calls are the *correct*
behaviour. Iteration count is only interpretable against that split, so it is
reported per shape rather than as one average.

**Success is a mechanical proxy** and is labelled as one. It asks whether the
answer named the expected file or symbol; it cannot tell whether the
explanation is right. Milestone 7 showed those come apart — an answer named
`filters.py` correctly while attributing the mechanism to the wrong symbol. A
stricter second reading is reported beside it: whether the expected *symbol*
was named, not merely the file around it. Nothing here scores the explanation,
because that needs a judge and an unvalidated judge is a number with nothing
behind it.

Tool-selection accuracy is defined mechanically too: a rejected call — a tool
that does not exist, or arguments that fail validation — is a wrong choice.

This is the Stage 1 baseline ADR-007 requires. Stage 2's multi-agent split is
justified only if it beats it.

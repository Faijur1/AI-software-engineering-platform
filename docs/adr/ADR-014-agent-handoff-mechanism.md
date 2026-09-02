# ADR-014 — How specialised agents hand work to each other

- **Status:** 🟡 **Draft — proposed, not decided.** Written so the options are
  on the table before Stage 2 milestones 2–4 begin.
- **Date:** 2026-09-02
- **Decision owner:** repository owner
- **Depends on:** [ADR-007](ADR-007-multi-agent-deferred.md) (entry condition),
  [ADR-013](ADR-013-cloud-llm-provider.md) (model capability)

## Context

Stage 2 introduces Manager, Research, Coding, Testing and Review agents. They
have to exchange work, and the shape of that exchange determines whether a bad
multi-agent run is debuggable.

[ADR-007](ADR-007-multi-agent-deferred.md) named the reason this matters:

> When a multi-agent run produces a bad answer, the cause may be poor
> retrieval, a bad tool result, a bad handoff between agents, or a bad plan.

Stage 1 removed three of those four from the unknowns. Retrieval is measured
(milestone 6). Tool results are recorded, including refusals. Plans are
captured. **The handoff is the one cause Stage 1 says nothing about**, so its
design is what decides whether Stage 2 is diagnosable or a guessing game.

What Stage 1 already provides:

- `Event.component` — the trace schema already distinguishes actors.
- `ToolContext.granted` — permissions are already per-context, so a Research
  agent that cannot reach the sandbox is a configuration, not new code.
- `AgentRun` — one row per run, with `tool_runs` beneath it.

What it does not: `AgentState` is an in-memory list of transcript strings,
there is no agent identity on `tool_runs` or `events`, and nothing models one
agent's output becoming another's input.

## Options

### Option 1 — Shared mutable `AgentState`

One object every agent reads and writes: `plan`, `findings`, `patch`,
`test_results`. The Manager passes it around; specialists mutate their part.

- **For:** closest to `docs/agents.md`'s existing `AgentState` sketch. Least
  code. Every agent sees everything, so no information is lost in transit.
- **Against:** any agent can overwrite any field, so a corrupted run cannot be
  attributed to one actor — precisely the diagnosis ADR-007 wants. Context
  grows without bound, which is the Stage 1 weakness ADR-007 already listed.
  With a weak model, "every agent sees everything" also means every agent sees
  every earlier mistake.

### Option 2 — Typed messages between agents

Each agent consumes a typed request and returns a typed result
(`ResearchResult`, `PatchProposal`, `TestReport`, `ReviewVerdict`), persisted.
The Manager routes them. No shared mutable object.

- **For:** every handoff is a stored, validated artefact, so a bad one names
  its producer and consumer. Each agent's context is scoped to what it was
  given, which bounds prompt size. Validation at the boundary means a
  malformed handoff is caught the way a malformed tool call already is.
- **Against:** more code and more schemas. Information the Manager did not
  think to forward is genuinely unavailable, so a badly designed message type
  becomes a hard ceiling.

### Option 3 — Blackboard: append-only shared log, scoped reads

One append-only record of contributions, each attributed to an agent. Agents
read a filtered view rather than mutating shared fields.

- **For:** attribution and immutability of option 2, with option 1's
  flexibility about what a later agent can look back at. Fits the existing
  `events` table closely.
- **Against:** "which slice does this agent read" becomes its own tuning
  problem, and getting it wrong reintroduces unbounded context quietly. Least
  precedent in this codebase.

## Recommendation (for discussion)

**Option 2, typed messages.**

The deciding argument is ADR-007's own: Stage 2 exists to be *measured against*
Stage 1, and a comparison is only meaningful if a regression can be attributed.
Typed handoffs make "the Research agent returned nothing useful" a fact visible
in a row, rather than an inference from a mutated blob.

It also composes with what already exists. A typed result validated at the
boundary is the same pattern as `invoke()` validating tool arguments before
execution — and that pattern has already proved its worth against a weak model,
which produces malformed structure constantly.

The cost is real: more schemas, and a message type that forgets a field becomes
a ceiling. That is mitigated by keeping every result linked to its `trace_id`,
so a later agent that needs more can be given a wider view without redesigning
the exchange.

If chosen, concretely:

- `agent_messages` table: `run_id`, `sequence`, `from_agent`, `to_agent`,
  `kind`, `payload jsonb`, `trace_id`.
- `agent_role` column added to `tool_runs` and `events`, so existing traces
  gain attribution without a new tracing mechanism.
- One Pydantic model per result type, validated on both sides of the handoff.
- The Manager owns routing and termination; specialists never call each other
  directly, so the call graph stays a star rather than a mesh.

## Open questions

- Does the Manager get its own iteration cap, separate from each specialist's?
  Two nested loops means two ways to run away.
- Can a specialist refuse a task it judges out of scope, and does that count as
  a failure or as correct behaviour in the metrics?
- Is Review advisory or blocking — can it send a patch back to Coding, and how
  many times before the run ends?
- Do specialists share one retrieval budget or hold their own?

## A prior question, now answered

This ADR assumes Stage 2 proceeds. **The milestone-1 baseline says: not yet,
but less decisively than first reported.**

Measured on `qwen2.5-coder:3b` after two corrections to the measurement itself
(see [`docs/README.md`](../README.md)): tool validity **1.000** — the model
never once broke the agent contract — with lookups at **0.750** and
investigations at **0.250**.

Two readings, and both deserve stating:

*Against acting now.* Handoffs are a coordination mechanism, and coordination
is the part that already works. What fails is reasoning across steps, and five
copies of the same model do not fix that.

*For acting.* A threefold gap between single-step and multi-step tasks is
exactly what a Manager decomposing an investigation into lookups would target.
The corrected numbers support this argument more than the original ones did,
when investigations scored zero and the case looked hopeless either way.

The counter to the second reading is that the Manager's decomposition is itself
a reasoning task run by the same model, and that handoffs add failure modes the
current benchmark does not measure at all.

So this ADR stays a draft and stays unimplemented until
[ADR-013](ADR-013-cloud-llm-provider.md) is resolved and the baseline is re-run
on a stronger model. If that run shows lookups strong and investigations still
weak, this ADR becomes the next thing to decide rather than the thing to defer.

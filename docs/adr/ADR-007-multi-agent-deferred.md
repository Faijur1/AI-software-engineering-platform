# ADR-007 — Multi-agent orchestration deferred to Stage 2

- **Status:** Accepted
- **Date:** 2026-09-01

## Context

The target architecture has specialised agents (Manager, Research, Coding,
Testing, Review) sharing one `AgentState`. Multi-agent coordination is the
hardest reliability problem in the system.

When a multi-agent run produces a bad answer, the cause may be poor retrieval, a
bad tool result, a bad handoff between agents, or a bad plan. If retrieval
quality is itself unproven, those causes cannot be told apart.

## Decision

Stage 1 implements exactly one agent loop — plan → select tool → execute →
observe → validate → finish — with a hard maximum-iteration limit. Specialised
agents arrive in Stage 2, only once retrieval and the tool layer are measured
and trusted.

## Alternatives considered

- **Build multi-agent immediately.** Higher ceiling, but debugging retrieval
  quality and agent coordination simultaneously is the failure mode this
  decision exists to avoid.
- **Never split.** A single agent may well be sufficient; Stage 2 should proceed
  only if measurements show the single agent failing in ways specialisation
  actually addresses.

## Consequences

**Positive**

- One loop to make reliable, with tool calls and state transitions traced.
- The Stage 1 evaluation harness produces the baseline that Stage 2 must beat.

**Negative**

- Single-agent context can grow large on complex tasks.
- Some Stage 1 agent code will be refactored when the Manager is introduced.

**Entry condition for Stage 2:** Stage 1's definition of done is met, and agent
metrics (task success rate, tool selection accuracy, iteration count) are
recorded — so any claim that multi-agent is better can be checked against
numbers rather than asserted.

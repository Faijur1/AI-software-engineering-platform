# ADR-005 — Kubernetes in Stage 3, for worker autoscaling

- **Status:** Proposed (Stage 3)
- **Date:** 2026-09-01

## Context

Two workloads have bursty, independent load:

- **ingestion/embedding workers** scale with repository size — indexing a large
  repository is a large, short-lived burst;
- **testing workers** scale with the number of concurrent agent runs.

The API itself has comparatively flat load. Scaling all components together
wastes capacity and does not address the actual bottleneck.

## Decision

Run the system on Kubernetes (AWS EKS) in Stage 3, and autoscale the ingestion
and testing worker Deployments **on queue backlog / Kafka consumer lag**, not on
CPU alone.

## Alternatives considered

- **Bigger single machine.** Simplest, but cannot scale the two bursty workloads
  independently and offers no isolation between them.
- **AWS ECS/Fargate.** Less operational overhead; adequate for this workload
  shape. Genuinely competitive, and rejected mainly because the sandbox needs
  fine-grained control over the container runtime.
- **Lambda.** Poor fit: indexing exceeds practical execution limits, and the
  sandbox needs a container runtime.

## Consequences

**Positive**

- Workers scale on the signal that actually indicates backlog.
- Namespaces and network policies reinforce the sandbox boundary (ADR-006).

**Negative**

- Substantial operational complexity: manifests, HPA tuning, cluster upgrades.
- Backlog-based HPA needs a custom metrics adapter, which is extra moving parts.

**Scope guard:** Kubernetes is for the API plus the two worker types. Splitting
the backend into microservices is explicitly *not* part of this decision.

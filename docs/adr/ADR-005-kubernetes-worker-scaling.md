# ADR-005 — Kubernetes in Stage 3, for worker autoscaling

- **Status:** Proposed, **not built, and deferred on measured grounds**
  (2026-09-04). See "Why this is not being built locally" at the end.
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

---

## Why this is not being built locally

Measured on the development machine rather than estimated:

| | |
| --- | --- |
| Host RAM | 7.7 GB total, 1.1 GB free under normal use |
| Docker VM ceiling | 3.7 GB |
| Kafka, KRaft single broker | ~1–1.5 GB |
| Docker Desktop Kubernetes control plane | ~1.5–2 GB |

Kafka and a Kubernetes control plane together come to roughly 3.2 GB of a 3.7 GB
ceiling, before Postgres, Redis, and the Ollama process the embedding model
needs. This is the same constraint that stopped `qwen2.5-coder:7b` running at
milestone 7, and it is a measurement rather than a guess.

There is also a design problem that memory merely postpones. ADR-006 makes the
Docker sandbox a security boundary. A testing worker running as a pod would have
to either mount the host Docker socket — which **destroys** that boundary, since
a pod holding the socket can escape to the host — or create Kubernetes Jobs with
the guarantees re-expressed as pod security: `runAsUser: 65534`,
`readOnlyRootFilesystem`, a NetworkPolicy denying egress, resource limits and
`activeDeadlineSeconds`. The second is correct and is the only version worth
building; it needs a second backend behind the sandbox runner interface, with
tests asserting the pod spec the way `build_command` is asserted today.

Neither is being attempted now. Autoscaling on consumer lag also has nothing to
scale: one developer indexing one repository generates no backlog, so the
signal this ADR is built around would read zero.

**Trigger:** revisit when there is a machine with headroom for a control plane
*and* a workload that produces measurable consumer lag. Managed Kubernetes on a
cloud provider remains a documented future step, not a built one.

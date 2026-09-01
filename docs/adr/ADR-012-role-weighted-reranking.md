# ADR-012 — Role-weighted reranking instead of a cross-encoder

- **Status:** Accepted
- **Date:** 2026-09-01
- **Supersedes:** the `bge-reranker-base` choice in [`docs/rag.md`](../rag.md)

## Context

[`docs/rag.md`](../rag.md) specified a cross-encoder (`bge-reranker-base`) as
the reranker, on the reasoning that seeing query and chunk together lets it
judge relevance in a way independent embeddings cannot. That reasoning is
sound, and it is why the cross-encoder was built and measured first rather than
dismissed.

Separately, milestone 6's benchmark produced a specific, repeated failure:
every question hybrid retrieval missed at K=5 missed the same way — tests and
documentation out-ranked the implementation. Asking for `OllamaEmbedder`
returned its two test files; asking how secrets are excluded returned
`docs/security.md`. Prose *about* code beats the code, because a test names the
thing under test repeatedly and a design document explains a mechanism in
exactly the vocabulary of a question about it.

Two candidate rerankers, then. Both were implemented and measured.

## Decision

Ship **role-weighted reranking**: classify each file by path as source, test,
docs or config, and scale the fused score by a per-role multiplier (source 1.0,
config 0.8, docs 0.7, test 0.6).

**Do not ship the cross-encoder.** The implementation has been removed rather
than left behind a flag, because dead code that crashes is worse than no code.

## Evidence

**Role weighting works, and it generalises.** Measured on the held-out question
set, which was written before any tuning and used for exactly one confirmatory
run:

| configuration | R@1 | R@5 | R@10 | MRR |
| --- | --- | --- | --- | --- |
| vector only | 0.429 | 0.607 | 0.714 | 0.664 |
| hybrid | 0.321 | 0.750 | 0.786 | 0.661 |
| **hybrid + role weighting** | **0.571** | **0.821** | **0.821** | **0.774** |

It is also the larger of the two effects: hybrid over vector-only gained
+0.143 R@5 and *nothing* on MRR, while role weighting added a further +0.071
R@5 and +0.113 MRR on top. It costs a dictionary lookup per candidate.

**The cross-encoder is not usable on this platform.** Three independent
findings, in the order they were discovered:

1. **It crashes.** `torch` 2.13.0+cpu segfaults — Windows exit code
   `-1073741819`, `ACCESS_VIOLATION` — partway through a benchmark run.
   Reproduced at 20 candidates per query and again at 8, with
   `torch.set_num_threads(1)`.
2. **It is far too slow.** Before crashing it logged **22–29 seconds to score
   20 query/chunk pairs** on CPU: roughly 1.1–1.5s per pair. An initial probe
   using short toy strings suggested ~200ms per pair; real chunks are up to 512
   tokens and the difference is an order of magnitude. Reranking alone would
   dominate a chat request that also has to generate an answer.
3. **It appears to prefer prose over code** — the opposite of what the measured
   failure needs. Asked "how are GitHub access tokens encrypted", it scored a
   paragraph of documentation **0.998** and the `encrypt_token` function itself
   **0.095**. This is one probe rather than a benchmark run, and is reported as
   such: the crash prevented a full measurement. It is a reason for caution,
   not a proven result.

Point 1 alone is disqualifying. Points 2 and 3 mean that fixing the crash would
probably not change the decision.

## Alternatives considered

- **Cross-encoder on a GPU.** Would address the latency, and possibly the
  crash. Not available here, and adding a hard GPU requirement to a project
  that otherwise runs on a laptop is a large cost for an unmeasured gain.
- **An LLM as reranker**, scoring chunks with `qwen2.5-coder` through the
  Ollama layer already in place. No new dependency, but slower still than the
  cross-encoder and with no measured quality advantage.
- **Boosting source files at retrieval time** rather than reranking. Rejected:
  it would change which chunks are *found*, not just their order, and would
  make it impossible to see what the retrievers actually returned — the
  inspector in milestone 8 depends on that distinction.
- **Excluding tests and docs from the index.** Much simpler, and wrong: "how do
  I run the worker" is often best answered by the README, and "what does this
  function guarantee" by its tests. Demoting is recoverable; excluding is not.

## Consequences

**Positive**

- A measured improvement, validated on questions never used for tuning.
- Negligible cost: one path classification and one multiplication per
  candidate, against 22+ seconds for the alternative.
- Fully explainable. A surprising ranking traces to a single multiplier, which
  matters for the inspector in milestone 8.
- No `torch`, no model download, no GPU. The project still runs on a laptop.

**Negative**

- **It is a blunt prior, not a relevance model.** It knows nothing about the
  query. A question genuinely about a test file is actively penalised, and
  `docs/` questions like "how do I run the worker" are made slightly harder.
- The weights are round numbers expressing an ordering, not fitted constants.
  They were chosen once and not tuned, deliberately, to keep the held-out
  result meaningful.
- Path-based classification will misfile unconventional layouts. It is
  predictable and checkable, which content-based inference would not be.
- The specified cross-encoder is not delivered. This ADR is the record of why.

**Revisit if** a GPU is available, or if the benchmark grows enough that
tuning the weights against a proper train/test split is worthwhile. The
`Reranker` protocol is unchanged, so either is a drop-in.

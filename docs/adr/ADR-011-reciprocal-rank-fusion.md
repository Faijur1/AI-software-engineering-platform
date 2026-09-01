# ADR-011 — Reciprocal Rank Fusion for merging hybrid results

- **Status:** Accepted
- **Date:** 2026-09-01
- **Refines:** the "normalise scores → merge" step in [`docs/rag.md`](../rag.md)

## Context

Hybrid retrieval runs two retrievers and has to combine their output into one
ranking. [`docs/rag.md`](../rag.md) states the requirement:

> Scores from the two are not comparable, so each result set is normalised
> before merging, and a chunk found by both is ranked more highly than one
> found by either alone.

The two score distributions are genuinely unrelated:

- **Cosine similarity** is bounded in roughly [0, 1], and in practice clusters
  tightly — measured on the real index, the top and tenth results for a query
  differ by around 0.05.
- **`ts_rank`** is unbounded, depends on term frequency *and* document length,
  and varies by orders of magnitude between queries. Measured on the same
  index: 0.0030 for one query's best match, 0.86 for another's.

The obvious reading of "normalise" is min-max scaling each list to [0, 1] and
taking a weighted sum. That has two failure modes that matter here:

1. **A single result normalises to 1.0.** If the keyword retriever returns one
   match, it is scaled to a perfect score regardless of how weak it was. This
   is common for identifier queries, which is precisely the case hybrid search
   exists to serve.
2. **The scaling is per-query and therefore unstable.** A query where every
   cosine similarity falls between 0.68 and 0.72 gets stretched across the full
   range, manufacturing large apparent differences from noise.

Both would need a hand-tuned weight between the two retrievers, and there is no
benchmark to tune against until milestone 6.

## Decision

Merge with **Reciprocal Rank Fusion**: each chunk scores
`Σ 1 / (k + rank_i)` over the lists it appears in, with `k = 60`.

Raw per-retriever scores and ranks are still recorded on every result and
returned by the API — they are what the RAG inspector displays — but they do
not participate in the merge.

`k = 60` is the published default from Cormack et al. (2009), left untuned.
Tuning it without the milestone-6 benchmark would be guessing.

## Alternatives considered

- **Min-max normalisation + weighted sum.** The literal reading of the design.
  Rejected for the two failure modes above, and because it needs a weight that
  cannot be chosen honestly yet.
- **Z-score normalisation.** Better behaved than min-max, but still assumes
  each list has a meaningful spread; it degenerates on the short result lists
  that identifier queries produce.
- **Take the union and sort by vector score.** Discards the keyword signal
  entirely, which is the whole point of running two retrievers.

## Consequences

**Positive**

- No cross-retriever scale calibration is needed at all, so nothing has to be
  re-tuned when the embedding model changes or the corpus grows.
- Agreement between independent retrievers is rewarded automatically: two
  contributions beat one, with no explicit boost term.
- Robust to the failure that motivated this milestone. Measured on the real
  index, "where is the OAuth callback handled" ranked the README first under
  vector-only search and never surfaced `github_callback`; under RRF the
  handler ranks first.
- Deterministic. Ties break on chunk id, so evaluation runs in milestone 6 are
  reproducible.

**Negative**

- **Fused scores are not interpretable in absolute terms.** A score of 0.032
  means "appeared near the top of both lists", not "3.2% relevant". They are
  comparable only within one query's results. The inspector therefore shows the
  raw per-retriever scores alongside, and the API returns both.
- Rank is a coarser signal than score: a result that is *far* better than the
  one below it gets no extra credit for the margin.
- The two retrievers are weighted equally. If measurement later shows one is
  consistently stronger for this corpus, a weighted RRF variant would be the
  change — but that is a milestone-6 decision, made from data.

**Revisit** when the labelled benchmark exists in milestone 6. That is the
first point at which any alternative can be compared rather than argued about.

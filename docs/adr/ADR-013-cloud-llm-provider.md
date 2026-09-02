# ADR-013 — Where answers are generated: local model or cloud LLM

- **Status:** ✅ **Accepted** — option 3, a provider behind an interface, is
  built and in use for chat. Superseded the draft on 2026-09-03.
- **Date:** 2026-09-02, decided 2026-09-03
- **Decision owner:** repository owner
- **Blocks:** trustworthy cited answers (milestone 7 is recorded as partial)

## Context

Milestone 7 built the whole answer path — retrieve, rerank, bounded context,
generate, verify citations — and measured it. Retrieval is good. Generation is
not, and the reason is the model, not the pipeline.

**What was measured** (details in [`docs/README.md`](../README.md)):

| | `qwen2.5-coder:1.5b` | `qwen2.5-coder:3b` |
| --- | --- | --- |
| Refuses an off-topic question | ❌ invented "45 mph" | ⚠️ see correction below |
| Cited any source | 0 of 3 questions | 1 of 3 (coverage 0.333) |
| Factual errors | yes | yes |

3b still said `is_secret_path` returns `False` for a secret path — it returns
`True` — with three `filters.py` chunks in front of it. **The evidence was
retrieved and misread.** No retrieval work fixes that.

`qwen2.5-coder:7b` needs 4.7 GB. The development machine has 7.7 GB total with
roughly 0.6 GB free once Docker, WSL and an editor are running, and Ollama
fails to allocate it. So the constraint is not a preference for local models —
it is that the machine cannot host one large enough.

Whether a 7B or larger model would cite reliably is **untested**. Nothing so
far is evidence either way, and this ADR should not be read as assuming it.

## The decision to make

Three viable options. They are not mutually exclusive; (3) subsumes the others.

### Option 1 — Stay local, accept the ceiling

Keep Ollama and `qwen2.5-coder:3b`. Ship chat labelled as unreliable, with the
existing warnings when an answer cites nothing.

- **For:** no new dependency, no cost, no data leaves the machine, no change to
  the trust boundary in [`docs/security.md`](../security.md).
- **Against:** the feature does not work well enough to be used for its stated
  purpose. Milestone 7 stays partial indefinitely.

### Option 2 — Cloud LLM only

Replace the Ollama chat client with a hosted API (Anthropic, OpenAI, or
similar). Embeddings stay local — `nomic-embed-text` works well and runs in
0.26 GB.

- **For:** almost certainly fixes citation and accuracy. No hardware
  requirement. Frees the memory budget entirely.
- **Against:** repository content leaves the machine (see Security below); adds
  per-query cost and an API key to manage; adds a hard network dependency to a
  feature that currently works offline.

### Option 3 — A provider interface, local by default, cloud opt-in

Put chat behind a `ChatProvider` protocol exactly as embeddings already sit
behind `EmbeddingProvider`. Ship both an Ollama and a cloud implementation.
Default to local; cloud is opted into **per repository**, not globally.

- **For:** the honest default (nothing leaves the machine unless asked); a
  public repository can use cloud while a private one does not; the benchmark
  can measure both providers against the same questions, so the claim "cloud is
  better" becomes measured rather than assumed.
- **Against:** more code than option 2, and two paths to keep working.

## Security and privacy — the part that needs a deliberate choice

This is the substance of the decision, not a footnote.

[`docs/security.md`](../security.md) currently assumes **everything stays
local**. Sending retrieved chunks to a third party breaks that assumption, and
it does so for the most sensitive content in the system: source code from
private repositories the user connected with a `repo`-scoped GitHub token.

What actually leaves the machine per question: 6–8 chunks, roughly 2,000 tokens
of verbatim source, plus the question. Not the whole repository — but reliably
the *most relevant* parts of it, which is worse per byte.

Points to settle before enabling this:

1. **Private repositories should be opt-in per repository**, not covered by a
   global flag. A user connecting a work repository has not consented to its
   code being sent anywhere by a setting they set months earlier for a
   different one.
2. **Provider retention terms matter and differ.** Whether prompts are retained
   or used for training must be checked against the specific provider and tier
   and recorded here, not assumed.
3. **The secret-exclusion filter becomes load-bearing in a new way.** It
   already prevents `.env` and keys from being indexed, and that is tested —
   but its failure mode changes from "a credential reaches a local model" to
   "a credential is transmitted to a third party".
4. **The API key is a new secret at rest**, and should be handled like
   `GITHUB_CLIENT_SECRET`: encrypted, never logged, never in a `NEXT_PUBLIC_`
   variable.
5. **Users should be able to see what was sent.** The inspector already shows
   exactly which chunks entered the context; that view becomes the audit trail
   and should say so explicitly when a cloud provider was used.

## Cost

Unmeasured, and the estimate below should be replaced by real numbers before
deciding. At ~2,000 input tokens and ~300 output tokens per question, a
mid-tier hosted model is on the order of fractions of a cent per question.
Indexing cost is unaffected — embeddings stay local.

The cost that matters is not per query but **per careless loop**: an agent
(milestone 9) asking many questions per run changes the arithmetic
substantially. Any decision here should be revisited against milestone 9 rather
than assumed to carry over.

## Recommendation (for discussion, not a decision)

**Option 3.** It is the only one that lets the choice be made per repository
and the difference be measured rather than argued. The extra work over option 2
is small — one protocol and one implementation — because the embedding layer
already established the pattern.

Concretely, if chosen:

- `app/llm/base.py` gains a `ChatProvider` protocol mirroring
  `EmbeddingProvider`; `app/llm/chat.py`'s Ollama call moves behind it.
- A `repositories.allow_cloud_llm` column, defaulting to false, with an
  explicit confirmation in the UI naming what will be sent.
- The evaluation harness gains answer-quality questions so "cloud is better" is
  a measurement. Note this needs **new held-out questions**: the current
  benchmark measures retrieval, not answers.
- `docs/security.md` gains a trust-boundary section for the cloud path.

## Open questions for the decision

- Which provider, and on what retention terms?
- Should cloud ever be permitted for a **private** repository, or only public?
- Does the answer change for milestone 9's agent, which will generate far more?
- Is a bigger *local* model on different hardware preferable to any cloud
  option, given the project's local-first stance?


---

## Decision, and what measurement supported it

A `ChatProvider` protocol with two implementations, selected by `LLM_PROVIDER`.
Chat and the agent loop resolve the provider through one function and neither
knows which is configured. Embeddings deliberately do **not** move: every stored
vector records the model that produced it, vectors from different models are not
comparable, and changing that means re-embedding the corpus as a separate
deliberate act.

The draft argued from a hand-run of three questions recorded in prose, which
could not be re-run and so could not be compared against. That gap is closed:
`eval/citation_probe.py` runs every provider over identical questions in one
process, so retrieval, context assembly and the citation checker are provably
the same and the model is the single variable.

| | `qwen2.5-coder:3b` | `gemini-3.6-flash` |
| --- | --- | --- |
| Cited at least one source | 1 of 3 | **3 of 3** |
| Mean citation coverage | 0.167 | **0.933** |
| All cited numbers exist | yes | yes |

That is the evidence for the decision. It confirms what milestone 7 concluded:
the pipeline was sound and the local model was the ceiling.

### A correction to the draft's own table

The draft recorded that `qwen2.5-coder:3b` "refuses cleanly" on the off-topic
control. It no longer does, and the reason is worth keeping. `docs/README.md`
now documents that very test and quotes the fabricated "47 mph" while describing
it. Retrieval hands the model a document *about* the false claim, and 3b repeats
it as fact — "approximately 47 mph. This information is provided in the source
code at [4]" — reproduced across three runs. Gemini declines and explains what
the source actually is.

So the original control stopped testing refusal and started testing something
harder: whether a model can tell a document describing a claim from the claim
itself. It is kept for that, and an uncontaminated control was added beside it.

### What is not decided here

**Per-repository opt-in is now built.** `repositories.allow_cloud_llm` defaults
to deny, `PATCH /repositories/{id}/settings` is the only way to grant it, and
`resolve_chat_provider` takes the permission as an argument rather than reading
configuration for it — so no caller can reach a remote provider without having
supplied a repository's answer. A denied repository is answered by the local
model with the downgrade stated in the response, rather than refused. See
`docs/security.md`.

**Cost and quota are unresolved.** The free tier is **20 requests per day, per
project, per model** — not per minute, and not per key. Verified by rotating the
key: the replacement inherited the exhausted allowance on the same model while
other models still answered. A compromised key can therefore be rotated freely
without buying back any quota. That is enough for interactive chat and far too little
for the agent benchmark, which needs 30–70 calls for one sweep. The agent
comparison this ADR would have wanted therefore **has no numbers**, and none are
invented here: every task in both attempts failed on quota at the first
iteration, with no tool ever called.

A related defect was found and fixed in the process: retrying a 429 spends the
budget it is waiting for, because each attempt is a billed request. A per-minute
limit clears and is worth retrying; a per-day one does not, and retrying it
burned a second model's entire allowance. The provider now tells them apart from
the `quotaId` in the error details.

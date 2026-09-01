# ADR-013 — Where answers are generated: local model or cloud LLM

- **Status:** 🟡 **Draft — proposed, not decided.** Written so the options are
  laid out before the decision is made, not to record one already taken.
- **Date:** 2026-09-02
- **Decision owner:** repository owner
- **Blocks:** trustworthy cited answers (milestone 7 is recorded as partial)

## Context

Milestone 7 built the whole answer path — retrieve, rerank, bounded context,
generate, verify citations — and measured it. Retrieval is good. Generation is
not, and the reason is the model, not the pipeline.

**What was measured** (details in [`docs/README.md`](../README.md)):

| | `qwen2.5-coder:1.5b` | `qwen2.5-coder:3b` |
| --- | --- | --- |
| Refuses an off-topic question | ❌ invented "45 mph" | ✅ refuses cleanly |
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

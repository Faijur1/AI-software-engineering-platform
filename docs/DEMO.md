# Demo script

A five-minute walkthrough, in the order that makes the strongest honest case.

The ordering principle: **lead with what is measured.** The retrieval numbers,
the inspector, the sandbox properties, CI and the consent gate are all things
that can be shown working and checked on the spot. Agent autonomy is not — its
decisions are weak on the local model and unmeasured on a hosted one. Showing
the guardrails rather than the autonomy is both a better story and a true one.

---

## Before the demo

```bash
docker compose up -d                       # postgres + redis
cd backend && ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
cd backend && ./.venv/Scripts/python.exe -m app.workers.run_worker   # 2nd terminal
cd frontend && npm run dev                                           # 3rd terminal
```

Checklist, each item earned by something that went wrong at least once:

- [ ] **A repository is already indexed.** Indexing takes minutes. Do not do it
      live unless the pipeline itself is the point.
- [ ] **Ollama is running** with `nomic-embed-text` pulled. Retrieval needs it
      regardless of which chat provider is configured, because embeddings are
      always local.
- [ ] **Check the hosted-model quota.** The free tier is 20 requests per day,
      per project, per model. A rehearsal plus a demo will exhaust it. Rehearse
      on one model and present on another:
      ```bash
      curl -s -o /dev/null -w "%{http_code}\n" \
        -H "x-goog-api-key: $GEMINI_API_KEY" \
        https://generativelanguage.googleapis.com/v1beta/models?pageSize=1
      ```
      A 429 on a generate call means the day's allowance is gone. Switching
      `GEMINI_MODEL` gives a fresh allowance; rotating the key does not.
- [ ] **Decide the consent state you want to open on.** Denied shows the gate
      working; allowed shows good answers. The script below uses both.
- [ ] **One uvicorn only.** `netstat -ano | findstr :8000` should list a single
      PID. Duplicates hold the port and serve stale code.

---

## The walkthrough

### 1. It knows whether it is healthy — 30 seconds

Open <http://localhost:3000>.

Dependency status is live, not decorative. Prove it rather than asserting it:

```bash
docker compose stop redis     # reload: "Degraded", with the real error
docker compose start redis    # reload: recovers
```

> Most projects claim a health check. This one names which dependency is down
> and refuses to report `ok` while any of them is.

### 2. Retrieval is measured — 60 seconds

Sign in, land on `/repositories`, open the **inspector** on the indexed
repository. Ask *"what stops secret files from being indexed"*.

Show that **every** candidate is listed, not just the ones used: which retriever
found each — vector, keyword, or both — what each scored it, and where the
reranker moved it.

Then `GET /evaluations`, or <http://localhost:8000/docs>, for the benchmark
behind it: 40 labelled questions across a tuning set and a held-out set, four
configurations measured over the same questions.

> The baselines are the point. Reporting hybrid alone would be an assertion;
> vector-only and keyword-only beside it are the evidence.

The endpoint serves the **held-out** set — written before any tuning, used for
confirmation only — and names it in the response, so a tuned score cannot be
mistaken for an honest one. Role weighting is the largest gain in the project:
R@5 0.643 → 0.786, MRR 0.607 → 0.702. If asked why those are lower than the
figures recorded at milestone 7: the corpus has grown by a third since, mostly
documentation, and prose competes with source. That is the exact failure role
weighting corrects, and the correction is still worth +0.143 R@5.

### 3. Consent before anything leaves the machine — 60 seconds

This is the part worth slowing down for.

On the repository row, turn the cloud toggle **off**. Ask a question in the chat
panel. The answer arrives — from the local model — and says so:

> *This repository has not been opted in to a cloud model, so the local model
> answered instead...*

Turn the toggle **on**. Ask the same question. Now a hosted model answers, with
citations into the retrieved sources.

Three things to draw out:

- **Default is deny.** A newly connected repository is never opted in, and
  enabling a provider in configuration opts nothing in.
- **Denied is answered locally, not refused.** Refusing would pressure people
  into granting permission to make their tool work, which is consent extracted
  by obstruction.
- **The downgrade is stated.** A worse answer for an invisible reason teaches
  the user the system is bad at this, which is the wrong lesson.

### 4. The sandbox is a real boundary — 60 seconds

Show `backend/app/sandbox/runner.py`, specifically `build_command`.

The argument list *is* the security policy: `--network none`, `--read-only`,
uid 65534, `--cap-drop ALL`, a pids limit, memory and swap caps, and an explicit
container kill on timeout. It is a separate function precisely so a test can
assert those flags verbatim.

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest -m sandbox -q
```

> These tests assert that the network is unreachable and the host filesystem
> invisible, from inside the container. A boundary nobody checks is a boundary
> nobody has.

### 5. The agent shows its work — 60 seconds

Start an agent run. While it works, show the **trace**: every iteration, every
tool call, every rejection, with durations. Then the **replay** panel, which
steps through the recorded run at real recorded pacing.

Say plainly what this does and does not demonstrate:

- The loop is bounded and cannot run forever.
- Every tool call is checked against a registry and a permission set before
  anything executes; a hallucinated tool name is a recorded rejection, not a
  crash.
- A proposed patch is shown as a diff behind an approval gate, and *validated*
  and *approved* are displayed as separate facts.
- **The quality of its decisions is the weak part**, and the benchmark says so.

> Claiming the agent is good would be the easiest thing to disprove in the room.
> Claiming its guardrails work is both stronger and checkable.

### 6. It is defended against itself — 30 seconds

Show the GitHub Actions tab, or `.github/workflows/ci.yml`.

Lint, types, unit tests, `alembic check`, integration tests against real
Postgres and Redis, the sandbox suite against a freshly built image, and the
frontend build — on every push.

> CI found five defects that had been passing on Windows and failing on Linux,
> including one where the sandbox could not delete its own workspace and leaked
> a directory per patch validation. That is a disk-exhaustion bug in the
> deployment target, found by automation rather than by a user.

---

## Questions worth pre-empting

**"How do you know retrieval is good?"** 40 labelled questions, split into a
tuning set and a held-out set that was never used for tuning. Held-out is the
honest number and it is the one reported.

**"Does it hallucinate?"** Citation coverage is measured per provider. The
local model cites about one answer in six; a hosted model covers most sentences.
Groundedness — whether a cited claim actually follows from its source — is *not*
measured, and is not claimed. That needs a judge, and an unvalidated judge is a
number with nothing behind it.

**"Can it fix bugs on its own?"** No, and the benchmark is the evidence rather
than an opinion. It finds files and symbols reasonably well and struggles with
multi-step investigation on the models tested so far.

**"Why is multi-agent not built?"** Because the case for it rests on a gap
measured on a weak local model, and the single-agent baseline on a stronger
model does not exist yet. Building the coordination first would make any
improvement unattributable.

---

## If something breaks

| Symptom | Cause | Recovery |
| --- | --- | --- |
| Chat returns a quota error | Daily allowance spent | Change `GEMINI_MODEL`, or turn the repository's toggle off and answer locally |
| Answers are poor and mention opting in | Repository not opted in | Turn the toggle on — this is the gate working |
| Indexing sits at `queued` | Worker not running | Start `app.workers.run_worker` |
| Health shows Degraded | A dependency is down | `docker compose up -d`; the panel names which one |
| Sandbox tests fail to start | Image not built | `docker build -f docker/sandbox.Dockerfile -t aisep-sandbox:latest .` |
| Stale behaviour after a code change | More than one uvicorn on :8000 | `taskkill /T /F /PID <pid>`, start one |

If a live model call fails mid-demo, say what happened and move on. The
project's whole posture is that measured limits are stated rather than hidden,
and a quota error is a much smaller problem than appearing to hide one.

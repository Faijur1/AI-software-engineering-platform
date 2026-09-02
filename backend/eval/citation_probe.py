"""Measure citation behaviour for each configured chat provider.

    python -m eval.citation_probe [--provider ollama gemini] [--repository owner/name]

Milestone 7 measured this by hand over three questions and recorded the result
in prose. That was enough to conclude the local model was not good enough, but
it cannot be re-run, so a second model cannot be compared against it. This makes
the same measurement repeatable and runs every provider over identical
questions in one process, which is the only way the comparison means anything:
retrieval, context assembly and the checker are then provably the same, and the
model is the single variable.

What is measured is **coverage, not groundedness** -- the share of sentences
carrying a citation, plus whether the cited numbers exist. A fabricated claim
with a valid citation number scores perfectly here, which is exactly the limit
milestone 7 recorded. Judging groundedness needs a judge, and an unvalidated
judge would be a number with nothing behind it.

Two controls, because the first version of this had a bug worth keeping a record
of. It asked the unladen-swallow question and scored any answer that cited a
source as a fabrication. That is wrong twice over.

It is wrong because a *correct* refusal cites: "the sources do not answer this,
and source [1] is documentation about the test itself" is the best possible
answer, and it carries a citation. Citing and fabricating are unrelated.

It is wrong because that question is now in the corpus. ``docs/README.md``
records the swallow test and quotes the fabricated "47 mph" from it, so
retrieval hands the model a document containing the wrong answer. That is no
longer a test of refusal; it is a test of whether the model can tell
documentation *about* a claim from the claim itself. Worth keeping -- it is a
harder and more realistic question -- but not as the refusal control.

So ``c-04`` keeps the swallow question with its true purpose stated, and
``c-05`` asks something the repository genuinely says nothing about. Neither is
scored by citation count. What is recorded is whether the answer asserted the
off-topic fact, which is the thing that matters, and the answers are saved in
full because this is a judgement a person should make rather than a regex.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import session_scope
from app.core.errors import ExternalServiceError
from app.llm.chat import SYSTEM_PROMPT, complete
from app.llm.gemini import GeminiChatProvider
from app.llm.ollama import OllamaEmbedder
from app.llm.ollama_chat import OllamaChatProvider
from app.llm.types import ChatProvider
from app.models.repository import Repository
from app.rag.citations import check_citations
from app.rag.context import build_context
from app.rag.reranker import RoleWeightedReranker
from app.rag.retriever import retrieve

RESULTS_DIR = Path(__file__).parent / "results"


@dataclass(frozen=True)
class Probe:
    id: str
    question: str
    # What a correct answer must name. Not scored automatically -- recorded so a
    # human reading the report can judge the claim rather than the formatting.
    expects: str
    # The repository cannot answer it. Correct behaviour is to decline -- which
    # may legitimately involve citing the source that shows why.
    should_refuse: bool = False
    # A substring whose presence means the model asserted the off-topic claim.
    # Deliberately narrow: it catches the specific fabrication seen in
    # milestone 7 rather than trying to judge refusal wording in general.
    fabrication_marker: str = ""


PROBES: tuple[Probe, ...] = (
    Probe(
        "c-01",
        "What stops secret files from being indexed?",
        "is_secret_path / classify in ingestion/filters.py, returning a skip reason",
    ),
    Probe(
        "c-02",
        "How are GitHub access tokens protected when stored?",
        "encrypt_token in core/security.py, Fernet encryption at rest",
    ),
    Probe(
        "c-03",
        "How does the agent stop itself running forever?",
        "the iteration cap in agent/engine.py, max_iterations_exceeded",
    ),
    Probe(
        "c-04",
        "What is the airspeed velocity of an unladen swallow?",
        "a refusal, noting the source is documentation about this test",
        should_refuse=True,
        # Deliberately not auto-scored. docs/README.md quotes "47 mph" while
        # describing a model fabricating it, so a *correct* refusal quotes the
        # number too -- and a substring check flagged exactly that as a
        # fabrication. The distinction is between asserting a claim and quoting
        # a document about the claim, which no regex here can draw. The answer
        # is saved in full and judged by reading. c-05 is the scored control.
        fabrication_marker="",
    ),
    Probe(
        "c-05",
        "What is the capital city of Mongolia?",
        "a refusal; nothing in this repository discusses Mongolia",
        should_refuse=True,
        fabrication_marker="ulaanbaatar",
    ),
)


@dataclass
class ProbeResult:
    id: str
    question: str
    provider: str
    model: str
    coverage: float
    citations_valid: bool
    cited_indices: list[int]
    invalid_indices: list[int]
    sources_offered: int
    duration_ms: int
    answer: str
    should_refuse: bool
    cited_anything: bool = False
    # False when the probe carries no marker: the answer is for reading, and a
    # verdict printed here would be an assertion nothing checked.
    auto_scored: bool = True
    # The answer asserted the off-topic claim. This, not the citation count, is
    # what makes a control answer wrong.
    asserted_off_topic: bool = False


@dataclass
class ProbeReport:
    repository: str
    commit: str | None
    generated_at: str
    kind: str = "citations"
    results: list[ProbeResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _provider(name: str) -> ChatProvider:
    settings = get_settings()
    if name == "gemini":
        return GeminiChatProvider(settings)
    return OllamaChatProvider(settings)


def run_probes(provider_name: str, *, repository_name: str | None = None) -> list[ProbeResult]:
    """Run every probe through one provider, sharing retrieval with the others."""
    provider = _provider(provider_name)
    results: list[ProbeResult] = []

    with session_scope() as session:
        query = select(Repository)
        if repository_name:
            owner, _, name = repository_name.partition("/")
            query = query.where(Repository.owner == owner, Repository.name == name)
        repository = session.execute(query).scalars().first()
        if repository is None:
            raise RuntimeError("No indexed repository found.")

        for probe in PROBES:
            retrieved = retrieve(
                session,
                OllamaEmbedder(),
                repository_id=repository.id,
                query=probe.question,
                limit=6,
                reranker=RoleWeightedReranker(),
            )
            if not retrieved.chunks:
                continue

            context = build_context(retrieved.chunks)
            try:
                completion = complete(
                    system=SYSTEM_PROMPT,
                    user=(
                        f"{context.prompt_context}\n\n"
                        f"Question: {probe.question}"
                    ),
                    temperature=0.1,
                    provider=provider,
                )
            except ExternalServiceError as exc:
                # Recorded rather than aborting the sweep: one provider being
                # unreachable must not discard the other's numbers.
                print(f"  {probe.id}: provider error: {exc.message}", file=sys.stderr)
                continue

            report = check_citations(completion.answer, context.sources)
            results.append(
                ProbeResult(
                    id=probe.id,
                    question=probe.question,
                    provider=provider.name,
                    model=completion.model,
                    coverage=round(report.citation_coverage, 3),
                    citations_valid=not report.invalid_indices,
                    cited_indices=sorted(report.cited_indices),
                    invalid_indices=sorted(report.invalid_indices),
                    sources_offered=context.included,
                    duration_ms=completion.duration_ms,
                    answer=completion.answer,
                    should_refuse=probe.should_refuse,
                    cited_anything=bool(report.cited_indices),
                    asserted_off_topic=bool(
                        probe.fabrication_marker
                        and probe.fabrication_marker in completion.answer.lower()
                    ),
                    auto_scored=bool(probe.fabrication_marker),
                )
            )
    return results


def format_results(results: list[ProbeResult]) -> str:
    lines: list[str] = []
    substantive = [r for r in results if not r.should_refuse]
    control = [r for r in results if r.should_refuse]

    if substantive:
        mean = sum(r.coverage for r in substantive) / len(substantive)
        cited = sum(1 for r in substantive if r.cited_anything)
        lines.append(f"  substantive questions   : {len(substantive)}")
        lines.append(f"  cited at least one src  : {cited}/{len(substantive)}")
        lines.append(f"  mean citation coverage  : {mean:.3f}")
        lines.append(
            "  all cited numbers exist : "
            f"{all(r.citations_valid for r in substantive)}"
        )
        lines.append(
            f"  median duration         : "
            f"{sorted(r.duration_ms for r in substantive)[len(substantive) // 2]} ms"
        )
    for result in control:
        if not result.auto_scored:
            verdict = "not auto-scored, read the answer"
        elif result.asserted_off_topic:
            verdict = "ASSERTED the off-topic claim"
        else:
            verdict = "declined"
        citing = "cited" if result.cited_anything else "no citation"
        lines.append(f"  control {result.id}           : {verdict} ({citing})")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(prog="eval.citation_probe", description=__doc__)
    parser.add_argument("--provider", nargs="+", default=["ollama", "gemini"])
    parser.add_argument("--repository")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    all_results: list[ProbeResult] = []

    for name in args.provider:
        if name == "gemini" and not settings.gemini_api_key.get_secret_value():
            print("Skipping gemini: GEMINI_API_KEY is not set.", file=sys.stderr)
            continue
        print(f"\n=== {name} ===")
        results = run_probes(name, repository_name=args.repository)
        all_results.extend(results)
        print(format_results(results))

    if not all_results:
        print("No results.", file=sys.stderr)
        return 2

    if not args.no_save:
        RESULTS_DIR.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = RESULTS_DIR / f"citations-{stamp}.json"
        payload = {
            "kind": "citations",
            "generated_at": datetime.now(UTC).isoformat(),
            "results": [asdict(r) for r in all_results],
        }
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"\nSaved {path.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

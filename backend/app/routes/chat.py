"""Answer a question about one indexed repository, with citations.

The whole RAG path in one request: retrieve, rerank, build a bounded context,
generate, then verify the citations that came back.

Synchronous, unlike indexing. Generation takes seconds rather than minutes, so
a request can wait for it -- but the timeout is the model's, and a slow answer
surfaces as an upstream error rather than a hung connection.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.llm.chat import answer_question
from app.llm.ollama import OllamaEmbedder
from app.models.chunk import CodeChunk
from app.models.repository import Repository
from app.rag.citations import check_citations
from app.rag.context import build_context
from app.rag.reranker import RoleWeightedReranker
from app.rag.retriever import retrieve
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    CitationCheck,
    CitedSource,
)

router = APIRouter(tags=["chat"])
logger = get_logger(__name__)


@router.post("/chat", response_model=ChatResponse, summary="Ask about a repository")
def chat(payload: ChatRequest, user: CurrentUser, session: DbSession) -> ChatResponse:
    """Answer a question from the caller's own indexed repository."""
    repository = session.execute(
        select(Repository).where(
            Repository.id == payload.repository_id, Repository.user_id == user.id
        )
    ).scalar_one_or_none()
    if repository is None:
        raise NotFoundError("Repository not found")

    embedded = session.execute(
        select(func.count())
        .select_from(CodeChunk)
        .where(
            CodeChunk.repository_id == repository.id,
            CodeChunk.embedding.is_not(None),
        )
    ).scalar_one()
    if embedded == 0:
        raise ValidationError(
            "This repository has no embedded chunks yet. Index it first."
        )

    # Role weighting is the default reranker: it is the configuration measured
    # best on held-out questions, and it costs microseconds. See
    # docs/README.md for the numbers behind that choice.
    result = retrieve(
        session,
        OllamaEmbedder(),
        repository_id=repository.id,
        query=payload.question,
        limit=payload.max_sources,
        reranker=RoleWeightedReranker(),
    )

    if not result.chunks:
        # An honest empty answer rather than an invented one. The model would
        # happily produce a plausible paragraph from no evidence at all.
        return ChatResponse(
            question=payload.question,
            repository_id=repository.id,
            answer=(
                "I could not find anything in this repository relevant to that "
                "question. Try naming a file, function, or symbol."
            ),
            sources=[],
            citations=CitationCheck(valid=True, citation_coverage=0.0),
            model="",
            reranker=result.reranker,
            retrieved_candidates=result.trace.fused_candidates,
            sources_offered=0,
            sources_included=0,
            estimated_context_tokens=0,
            duration_ms=0,
            notes=[*result.trace.notes, "No relevant sources were retrieved."],
        )

    context = build_context(result.chunks)
    completion = answer_question(payload.question, context.prompt_context)
    citations = check_citations(completion.answer, context.sources)

    logger.info(
        "chat_answered",
        repository=repository.full_name,
        sources=context.included,
        cited=len(citations.cited_indices),
        invalid_citations=len(citations.invalid_indices),
        duration_ms=completion.duration_ms,
    )

    notes = list(result.trace.notes)
    if context.dropped_for_budget:
        notes.append(
            f"{context.dropped_for_budget} retrieved chunk(s) did not fit the "
            "context budget and were not shown to the model."
        )
    if citations.sentences and citations.citation_coverage == 0.0:
        # A confident, entirely uncited answer is the shape a fabrication
        # takes. Stated rather than left for the reader to notice.
        notes.append(
            "The answer cited no sources at all. Nothing in it has been traced "
            "to the retrieved code -- verify it against the sources below."
        )
    elif 0.0 < citations.citation_coverage < 0.5:
        notes.append(
            "Fewer than half the answer's sentences cite a source. The uncited "
            "parts are not backed by retrieved code."
        )
    if citations.invalid_indices:
        # Surfaced rather than silently corrected: an answer citing evidence
        # that does not exist is a fact the reader needs.
        notes.append(
            "The answer cited sources that do not exist: "
            f"{citations.invalid_indices}. Treat it with suspicion."
        )

    cited = set(citations.cited_indices)
    return ChatResponse(
        question=payload.question,
        repository_id=repository.id,
        answer=completion.answer,
        sources=[
            CitedSource(
                index=source.index,
                chunk_id=source.chunk_id,
                file_path=source.file_path,
                symbol=source.symbol,
                start_line=source.start_line,
                end_line=source.end_line,
                content=source.content,
                cited=source.index in cited,
            )
            for source in context.sources
        ],
        citations=CitationCheck(
            valid=citations.is_valid,
            invalid_indices=citations.invalid_indices,
            citation_coverage=round(citations.citation_coverage, 3),
        ),
        model=completion.model,
        reranker=result.reranker,
        retrieved_candidates=result.trace.fused_candidates,
        sources_offered=context.offered,
        sources_included=context.included,
        estimated_context_tokens=context.estimated_tokens,
        duration_ms=completion.duration_ms,
        notes=notes,
    )

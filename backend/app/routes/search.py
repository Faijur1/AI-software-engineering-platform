"""Retrieval over an indexed repository.

Synchronous: retrieval is two indexed queries plus one embedding call, so it
answers in well under a second and does not need the job machinery that
indexing does.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession
from app.core.errors import NotFoundError, ValidationError
from app.llm.ollama import OllamaEmbedder
from app.models.chunk import CodeChunk
from app.models.repository import Repository
from app.rag.reranker import RoleWeightedReranker
from app.rag.retriever import retrieve
from app.rag.roles import classify_role
from app.rag.types import RetrievedChunk
from app.schemas.search import SearchHit, SearchRequest, SearchResponse

router = APIRouter(prefix="/repositories", tags=["search"])


@router.post(
    "/{repository_id}/search",
    response_model=SearchResponse,
    summary="Hybrid search over an indexed repository",
)
def search_repository(
    repository_id: uuid.UUID,
    payload: SearchRequest,
    user: CurrentUser,
    session: DbSession,
) -> SearchResponse:
    """Search one repository the caller owns.

    Scoped to a single repository by design. Retrieval never spans
    repositories, so one user's code cannot surface in another's results
    (docs/security.md).
    """
    repository = session.execute(
        select(Repository).where(
            Repository.id == repository_id, Repository.user_id == user.id
        )
    ).scalar_one_or_none()
    if repository is None:
        raise NotFoundError("Repository not found")

    embedded = session.execute(
        select(func.count())
        .select_from(CodeChunk)
        .where(
            CodeChunk.repository_id == repository_id,
            CodeChunk.embedding.is_not(None),
        )
    ).scalar_one()
    if embedded == 0:
        # Distinguishing "nothing indexed" from "nothing matched" matters: the
        # first is a missing step the user can fix, the second is a real answer.
        raise ValidationError(
            "This repository has no embedded chunks yet. Index it first."
        )

    # The same reranker chat uses. The inspector exists to explain what the
    # answer pipeline actually did, so search must not quietly run a different
    # configuration from the one that produces answers.
    result = retrieve(
        session,
        OllamaEmbedder(),
        repository_id=repository_id,
        query=payload.query,
        limit=payload.limit,
        reranker=RoleWeightedReranker(),
    )

    return SearchResponse(
        query=payload.query,
        repository_id=repository_id,
        results=[_to_hit(chunk) for chunk in result.chunks],
        candidates=(
            [_to_hit(chunk) for chunk in result.candidates]
            if payload.include_candidates
            else None
        ),
        vector_candidates=result.trace.vector_candidates,
        keyword_candidates=result.trace.keyword_candidates,
        fused_candidates=result.trace.fused_candidates,
        notes=result.trace.notes,
        reranker=result.reranker,
        reranker_is_passthrough=result.reranker_is_passthrough,
    )


def _to_hit(chunk: RetrievedChunk) -> SearchHit:
    """Render one candidate with everything needed to explain its rank."""
    return SearchHit(
        chunk_id=chunk.chunk_id,
        file_path=chunk.file_path,
        symbol=chunk.symbol,
        kind=chunk.kind,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
        content=chunk.content,
        method=chunk.method,
        fused_score=chunk.fused_score,
        vector_score=chunk.vector_score,
        vector_rank=chunk.vector_rank,
        keyword_score=chunk.keyword_score,
        keyword_rank=chunk.keyword_rank,
        rerank_score=chunk.rerank_score,
        selected=chunk.selected,
        role=classify_role(chunk.file_path).value,
    )

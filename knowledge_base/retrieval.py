"""Replaceable retrieval interface and lightweight lexical implementation."""

from __future__ import annotations

import heapq
import re
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass

from django.conf import settings
from django.db.models import Q

from .models import KnowledgeChunk


_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "could",
    "do", "does", "for", "from", "how", "i", "in", "is", "it", "me",
    "my", "of", "on", "or", "please", "tell", "that", "the", "this",
    "to", "was", "what", "when", "where", "which", "who", "why", "with",
    "would", "you",
}
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:[._+-][a-z0-9]+)*")


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: int
    document_id: int
    source_identifier: str
    title: str
    chunk_index: int
    content: str
    score: float

    def source_metadata(self) -> dict:
        return {
            "document_id": self.document_id,
            "source_identifier": self.source_identifier,
            "title": self.title,
            "chunk_index": self.chunk_index,
            "score": self.score,
        }


class KnowledgeRetriever(ABC):
    @abstractmethod
    def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        min_relevance: float | None = None,
    ) -> list[RetrievedChunk]:
        """Return relevant chunks ordered by score and stable source position."""
        ...


class LexicalRetriever(KnowledgeRetriever):
    """Term-coverage retrieval with no model or external service dependency."""

    def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        min_relevance: float | None = None,
    ) -> list[RetrievedChunk]:
        top_k = int(top_k if top_k is not None else settings.RAG_TOP_K)
        min_relevance = float(
            min_relevance
            if min_relevance is not None
            else settings.RAG_MIN_RELEVANCE
        )
        if top_k <= 0:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []
        query_counts = Counter(query_tokens)

        candidate_filter = Q()
        for term in sorted(query_counts):
            candidate_filter |= Q(content__icontains=term)

        candidates = (
            KnowledgeChunk.objects
            .filter(document__status="INDEXED")
            .filter(candidate_filter)
            .select_related("document")
            .order_by("document_id", "chunk_index")
        )

        best: list[tuple[float, int, int, int, RetrievedChunk]] = []
        for chunk in candidates.iterator(chunk_size=200):
            score = _term_coverage(query_counts, Counter(_tokenize(chunk.content)))
            if score < min_relevance:
                continue
            result = RetrievedChunk(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                source_identifier=chunk.document.source_identifier,
                title=chunk.document.filename,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                score=round(score, 4),
            )
            rank = (
                result.score,
                -result.document_id,
                -result.chunk_index,
                -result.chunk_id,
                result,
            )
            if len(best) < top_k:
                heapq.heappush(best, rank)
            elif rank[:4] > best[0][:4]:
                heapq.heapreplace(best, rank)

        return sorted(
            (entry[-1] for entry in best),
            key=lambda result: (
                -result.score,
                result.document_id,
                result.chunk_index,
                result.chunk_id,
            ),
        )


def _tokenize(text: str) -> list[str]:
    return [
        token
        for token in _TOKEN_PATTERN.findall((text or "").lower())
        if token not in _STOP_WORDS and len(token) > 1
    ]


def _term_coverage(query_counts: Counter, content_counts: Counter) -> float:
    query_term_count = sum(query_counts.values())
    if not query_term_count:
        return 0.0
    matched = sum(
        min(count, content_counts.get(term, 0))
        for term, count in query_counts.items()
    )
    return matched / query_term_count

"""Deterministic request routing for local, RAG, online, and action paths."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum

from knowledge_base.retrieval import KnowledgeRetriever, RetrievedChunk
from knowledge_base.services import get_retriever

from .policy import AssistantResponsePolicy, Capability, CapabilityRegistry, ResponseMode


logger = logging.getLogger(__name__)


class QueryRoute(str, Enum):
    LOCAL = "local"
    LOCAL_RAG = "local_rag"
    ONLINE = "online"
    ACTION = "action"
    CLARIFICATION = "clarification"


@dataclass(frozen=True)
class QueryRouteDecision:
    route: QueryRoute
    response_mode: ResponseMode
    required_capability: Capability | None = None
    rag_results: tuple[RetrievedChunk, ...] = field(default_factory=tuple)


_EXPLICIT_ONLINE_PATTERNS = (
    r"\b(?:search|browse)\s+(?:the\s+)?(?:web|internet|online)\b",
    r"\bsearch\s+online\s+for\b",
    r"\blook\s+(?:this\s+)?up\s+online\b",
    r"\bcheck\s+(?:the\s+)?(?:web|internet)\b",
)

_FRESHNESS_PATTERN = re.compile(
    r"\b(?:today(?:'s)?|latest|current(?:ly)?|live|right now|this week|"
    r"recent(?:ly)?|breaking)\b"
)

_ONLINE_SUBJECT_PATTERN = re.compile(
    r"\b(?:news|headlines?|weather|traffic|prices?|stocks?|shares?|bitcoin|"
    r"crypto(?:currency)?|scores?|match(?: results?)?|game results?|schedules?|"
    r"timetables?|flight status|flights?|exchange rates?|office holders?|"
    r"president|prime minister|governor|mayor|releases?|versions?|availability)\b"
)


class QueryRouter:
    """Route one query without LLM classification or mutable global state."""

    def __init__(self, retriever: KnowledgeRetriever | None = None):
        self._retriever = retriever

    def decide(
        self,
        query: str,
        *,
        registry: CapabilityRegistry,
        response_mode: ResponseMode | None = None,
        required_capability: Capability | None = None,
    ) -> QueryRouteDecision:
        if response_mode is None:
            response_mode, required_capability = AssistantResponsePolicy.classify(query)

        if response_mode == ResponseMode.CLARIFICATION:
            return QueryRouteDecision(
                QueryRoute.CLARIFICATION,
                response_mode,
                required_capability,
            )

        if response_mode == ResponseMode.ACTION:
            if required_capability == Capability.ONLINE_RETRIEVAL:
                return QueryRouteDecision(
                    QueryRoute.ONLINE,
                    response_mode,
                    required_capability,
                )

            rag_results = ()
            if (
                required_capability == Capability.LOCAL_RAG
                and registry.is_available(Capability.LOCAL_RAG)
            ):
                rag_results = self._retrieve(query)
            return QueryRouteDecision(
                QueryRoute.ACTION,
                response_mode,
                required_capability,
                rag_results,
            )

        if self.requires_online(query):
            return QueryRouteDecision(
                QueryRoute.ONLINE,
                response_mode,
                Capability.ONLINE_RETRIEVAL,
            )

        if registry.is_available(Capability.LOCAL_RAG):
            rag_results = self._retrieve(query)
            if rag_results:
                return QueryRouteDecision(
                    QueryRoute.LOCAL_RAG,
                    response_mode,
                    Capability.LOCAL_RAG,
                    rag_results,
                )

        return QueryRouteDecision(
            QueryRoute.LOCAL,
            response_mode,
            required_capability,
        )

    @staticmethod
    def requires_online(query: str) -> bool:
        normalized = " ".join((query or "").lower().split())
        if any(re.search(pattern, normalized) for pattern in _EXPLICIT_ONLINE_PATTERNS):
            return True
        return bool(
            _FRESHNESS_PATTERN.search(normalized)
            and _ONLINE_SUBJECT_PATTERN.search(normalized)
        )

    def _retrieve(self, query: str) -> tuple[RetrievedChunk, ...]:
        try:
            retriever = self._retriever or get_retriever()
            return tuple(retriever.retrieve(query))
        except Exception:
            logger.exception("Local knowledge retrieval failed; routing to local LLM")
            return ()

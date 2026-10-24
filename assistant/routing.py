"""Deterministic request routing for local, RAG, online, and action paths."""

from __future__ import annotations

from assistant import latency_trace as latency
import logging
import re
import time
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
    rag_latency_ms: int = field(default=0, compare=False)
    retrieval_query: str | None = None


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

_OFFLINE_REQUEST = re.compile(
    r"\b(?:answer offline|offline only|without (?:the )?internet|from your (?:memory|knowledge)|(?:do not|don't) (?:search|browse|go online))\b", re.I,
)

_RAG_WRAPPERS = re.compile(
    r"^(?:(?:please )?(?:(?:can|could|would) you )?)?(?:"
    r"what (?:does|do) (?:my|our|the) (?:local )?knowledge base (?:say|contain) (?:about|on)|"
    r"according to (?:my|our|the) (?:local )?(?:notes|documents|files|knowledge base)[,:]?|"
    r"(?:search|check|consult) (?:my|our|the) (?:local )?(?:documents|files|notes|knowledge base) for|"
    r"look in (?:my|our|the) (?:local )?(?:files|documents|notes|knowledge base) for|"
    r"in (?:our|my|this|the) project[,:]?"
    r")\s*", re.IGNORECASE,
)


def normalize_rag_query(query: str) -> str:
    """Strip only recognized local-request wrappers; retain topical wording/scoring."""
    candidate = " ".join(query.split())
    match = _RAG_WRAPPERS.match(candidate)
    if not match:
        return query  # Implicit text-chat retrieval stays byte-for-byte unchanged.
    candidate = candidate[match.end():].strip()
    candidate = re.sub(
        r"^(?:what|how|which|where|why|when)\s+(?:(?:is|are|was|were|does|do|did)\s+)?(?:(?:we|i|you)\s+)?",
        "", candidate, flags=re.IGNORECASE,
    )
    return candidate.rstrip(" .?!")


class QueryRouter:
    """Route one query without LLM classification or mutable global state."""

    def __init__(self, retriever: KnowledgeRetriever | None = None):
        self._retriever = retriever

    @latency.span("routing")
    def decide(
        self,
        query: str,
        *,
        registry: CapabilityRegistry,
        response_mode: ResponseMode | None = None,
        required_capability: Capability | None = None,
        allow_implicit_rag: bool = True,
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
                if not self.requires_online(query):
                    response_mode = ResponseMode.NORMAL
                    required_capability = None
                else:
                    return QueryRouteDecision(
                        QueryRoute.ONLINE,
                        response_mode,
                        required_capability,
                    )
            else:
                return self._action_decision(query, registry, response_mode, required_capability)

        if self.requires_online(query):
            return QueryRouteDecision(
                QueryRoute.ONLINE,
                response_mode,
                Capability.ONLINE_RETRIEVAL,
            )

        return self._local_decision(query, registry, response_mode, required_capability, allow_implicit_rag)

    def _action_decision(self, query, registry, response_mode, required_capability):
        rag_results = ()
        retrieval_query = None
        if (
            required_capability == Capability.LOCAL_RAG
            and registry.is_available(Capability.LOCAL_RAG)
        ):
            rag_started = time.perf_counter()
            retrieval_query = normalize_rag_query(query)
            rag_results = self._retrieve(retrieval_query)
            rag_latency_ms = round((time.perf_counter() - rag_started) * 1000)
        else:
            rag_latency_ms = 0
        return QueryRouteDecision(
            QueryRoute.ACTION,
            response_mode,
            required_capability,
            rag_results,
            rag_latency_ms,
            retrieval_query,
        )

    def _local_decision(self, query, registry, response_mode, required_capability, allow_implicit_rag):
        rag_latency_ms = 0
        retrieval_query = None
        if allow_implicit_rag and registry.is_available(Capability.LOCAL_RAG):
            rag_started = time.perf_counter()
            retrieval_query = normalize_rag_query(query)
            rag_results = self._retrieve(retrieval_query)
            rag_latency_ms = round((time.perf_counter() - rag_started) * 1000)
            if rag_results:
                return QueryRouteDecision(
                    QueryRoute.LOCAL_RAG,
                    response_mode,
                    Capability.LOCAL_RAG,
                    rag_results,
                    rag_latency_ms,
                    retrieval_query,
                )

        return QueryRouteDecision(
            QueryRoute.LOCAL,
            response_mode,
            required_capability,
            (),
            rag_latency_ms,
            retrieval_query,
        )

    @staticmethod
    def requires_online(query: str, *, respect_offline: bool = True) -> bool:
        normalized = " ".join((query or "").lower().split())
        if respect_offline and _OFFLINE_REQUEST.search(normalized):
            return False
        if any(re.search(pattern, normalized) for pattern in _EXPLICIT_ONLINE_PATTERNS):
            return True
        # Personal/project requests must never leak into public search merely
        # because their documents mention a price, release or schedule.
        if _RAG_WRAPPERS.match(normalized) or re.search(r"\b(?:my|our|attached|uploaded)\b|\bthis (?:project|repository)\b|\blocal (?:files|documents|notes)\b", normalized):
            return False
        if re.fullmatch(r"(?:hi|hello|hey|thanks|thank you|good morning|good evening|how are you(?: today)?)[ .!?]*", normalized):
            return False
        if re.search(r"\b(?:alternating current|direct current|current transformer|current density|current flow|current loop|live variables|live wire)\b", normalized) and re.search(r"\b(?:explain|define|what is|how does|how do|meaning)\b", normalized):
            return False
        if re.search(r"\b(?:who was|who were|history of|historical|in (?:19\d{2}|200\d))\b", normalized):
            return False
        if re.search(r"^(?:what (?:is|are) (?:an? |the )?(?:news|weather|forecast|schedule|ranking|release|president|ceo|stock market|price elasticity|version control|(?:electrical |electric )?current)|what does .+ mean)[?.!]*$", normalized):
            return False
        if re.search(r"^(?:explain\b|define\b|what (?:does .+ mean|is (?:an? |the concept of )?)|how (?:does|do)\b)", normalized):
            if re.search(r"\b(?:mean|meaning|work|works|concept|electrical current|electric current|live variables)\b", normalized) and not _FRESHNESS_PATTERN.search(normalized):
                return False
            if normalized.startswith(("explain", "define")) and not _FRESHNESS_PATTERN.search(normalized):
                return False
        return bool(
            re.search(r"\b(?:news|headlines?|weather|forecast|traffic|prices?|stocks?|bitcoin|crypto(?:currency)?|exchange rates?|scores?|standings?|rankings?|schedules?|timetables?|availability|flight status|service status|outage|releases?)\b", normalized)
            or re.search(r"\b(?:ceo|president|prime minister|governor|mayor|chief executive officer|chairperson)\b", normalized)
            or re.search(r"\bwho (?:runs|leads)\b", normalized)
            or re.search(r"\b(?:won|winning)\b.*\b(?:match|game|election|tournament)\b", normalized)
            or re.search(r"\bhow much\b.*\b(?:costs?|is|are)\b|\bnext\b.*\b(?:match|game|train|flight|election)\b", normalized)
            or _FRESHNESS_PATTERN.search(normalized)
            and not re.search(r"\b(?:current|latest|live)\s+(?:mean|variable|electric|flow)\b", normalized)
            or re.search(r"\b(?:is|are)\b.*\b(?:available|open|down|released|in stock)\b", normalized)
        )

    def _retrieve(self, query: str) -> tuple[RetrievedChunk, ...]:
        try:
            retriever = self._retriever or get_retriever()
            return tuple(retriever.retrieve(query))
        except Exception:
            logger.exception("Local knowledge retrieval failed; routing to local LLM")
            return ()

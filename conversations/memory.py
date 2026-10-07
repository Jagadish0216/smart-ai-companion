"""Bounded, replaceable retrieval of earlier user statements in one conversation."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
import re

from django.conf import settings
from django.db.models import Q, Subquery
from django.db.models.functions import Substr

from .models import Message


# Hard bounds on SQLite candidate scanning, Python work and per-row text transfer.
SCAN_MESSAGES = 500
MAX_CANDIDATES = 200
MAX_MESSAGE_CHARS = 4000
MAX_QUERY_TERMS = 16
_TOKEN = re.compile(r"[a-z0-9]+(?:[._+-][a-z0-9]+)*")
_STOP_WORDS = set("""a an and are as at be been by can could did do does for from
    had has have how i in is it its me my of on or our please said say tell that
    the their them this to us was were what when where which who why with would
    you your s call called name named use uses using""".split())


def tokenize(text: str) -> set[str]:
    # GPIO18 and GPIO 18 denote the same technical token, without fuzzy matching.
    text = re.sub(r"\bgpio\s+(\d+)\b", r"gpio\1", text.lower())
    tokens = set(_TOKEN.findall(text))
    if any(re.fullmatch(r"gpio\d+", token) for token in tokens):
        tokens.add("gpio")
    # Preserve full identifiers and also match their meaningful components.
    tokens |= {part for token in tokens for part in re.split(r"[._+-]", token)}
    return {token for token in tokens if token not in _STOP_WORDS and len(token) <= 64}


@dataclass(frozen=True)
class MemoryMessage:
    message_id: int
    content: str
    timestamp: object
    score: float


class ConversationMemoryRetriever(ABC):
    @abstractmethod
    def retrieve(self, query: str, *, conversation_id: int, before_id: int,
                 recent_ids: list[int], recent_texts: list[str] | None = None) -> list[MemoryMessage]:
        """Retrieve only earlier, non-recent USER statements in this conversation."""
        ...


class LexicalConversationMemoryRetriever(ConversationMemoryRetriever):
    def retrieve(self, query: str, *, conversation_id: int, before_id: int,
                 recent_ids: list[int], recent_texts: list[str] | None = None) -> list[MemoryMessage]:
        top_k = max(0, int(settings.CONVERSATION_MEMORY_TOP_K))
        budget = max(0, int(settings.CONVERSATION_MEMORY_MAX_CHARS))
        terms = sorted(tokenize(query))[:MAX_QUERY_TERMS]
        if not top_k or not budget or not terms:
            return []

        eligible = Message.objects.filter(
            conversation_id=conversation_id, sender="USER", id__lt=before_id,
        ).exclude(id__in=recent_ids)
        # Limit the scan even if no terms match. The existing conversation FK
        # index and row IDs suffice; no cache/index table or migration is needed.
        scan_ids = eligible.order_by("-id").values("id")[:SCAN_MESSAGES]
        term_filter = Q()
        for term in terms:
            term_filter |= Q(excerpt__icontains=term)
            if re.fullmatch(r"gpio\d+", term):
                term_filter |= Q(excerpt__icontains="gpio " + term[4:])
        candidates = (
            Message.objects.filter(id__in=Subquery(scan_ids))
            .annotate(excerpt=Substr("text", 1, MAX_MESSAGE_CHARS))
            .filter(term_filter).order_by("-timestamp", "-id")
            .values("id", "excerpt", "timestamp")[:MAX_CANDIDATES]
        )
        ranked = []
        query_terms = set(terms)
        excluded_texts = {query[:MAX_MESSAGE_CHARS].strip().casefold()}
        excluded_texts.update(text[:MAX_MESSAGE_CHARS].strip().casefold() for text in recent_texts or [])
        for row in candidates:
            content = row["excerpt"].strip()
            if not content or content.casefold() in excluded_texts:
                continue
            score = len(query_terms & tokenize(content)) / len(query_terms)
            if score >= settings.CONVERSATION_MEMORY_MIN_RELEVANCE:
                ranked.append(MemoryMessage(row["id"], content, row["timestamp"], score))
        ranked.sort(key=lambda item: (-item.score, -item.timestamp.timestamp(), -item.message_id))
        selected = []
        seen = set()
        for item in ranked:
            if len(selected) >= top_k or budget <= 0:
                break
            key = item.content.casefold()
            if key in seen:
                continue
            seen.add(key)
            content = item.content[:budget]
            if len(content) < len(item.content) and " " in content:
                content = content.rsplit(" ", 1)[0]
            content = content.strip()
            if content:
                selected.append(replace(item, content=content))
                budget -= len(content)
        return selected


def get_memory_retriever() -> ConversationMemoryRetriever:
    return LexicalConversationMemoryRetriever()


def build_memory_history(messages: list[MemoryMessage]) -> list[dict]:
    """Replay selected earlier USER statements chronologically, without prompt rules."""
    chronological = sorted(messages, key=lambda item: (item.timestamp, item.message_id))
    return [{"role": "user", "content": item.content} for item in chronological]

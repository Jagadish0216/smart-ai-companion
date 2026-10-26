"""Request-scoped policy shared by browser, streaming and hardware voice."""
import re
from django.conf import settings
from .retrieval import LexicalRetriever


def factual_query(query):
    query = re.sub(r'^(?:answer offline(?: only)?|offline only|online only)\s*[:,-]?\s*', '', query, flags=re.I)
    if re.fullmatch(r'(?:hi|hello|hey|thanks|thank you|how are you(?: today)?)[?!. ]*', query.strip(), re.I):
        return False
    if re.match(r'(?:latest|current|recent)\b', query.strip(), re.I):
        return True
    return bool(re.match(r'(?:what|which|who|where|when|why|how|is|are|does|do|explain|define|describe|tell me about)\b', query.strip(), re.I))


def resolve_mode(query, mode=None):
    from assistant.routing import _OFFLINE_REQUEST
    if _OFFLINE_REQUEST.search(query):
        return 'offline_only'
    if re.search(r'\bonline only\b', query, re.I):
        return 'online_only'
    if re.search(r'\b(?:search|browse) (?:the )?(?:web|internet|online)\b', query, re.I):
        return 'online_preferred'
    value = mode or getattr(settings, 'KNOWLEDGE_MODE', 'online_preferred')
    if value not in {'online_preferred', 'offline_only', 'online_only'}:
        raise ValueError('Invalid knowledge mode')
    return value


def resolve_knowledge_route(query, decision, registry, mode, document_ids):
    from assistant.routing import QueryRoute, QueryRouteDecision, requests_local_knowledge
    from assistant.policy import Capability
    if document_ids is not None:
        results = LexicalRetriever(document_ids=document_ids, manual_only=True).retrieve(query)
        return QueryRouteDecision(QueryRoute.LOCAL_RAG, decision.response_mode, Capability.LOCAL_RAG, tuple(results))
    if decision.route in (QueryRoute.ACTION, QueryRoute.CLARIFICATION):
        return decision
    private = requests_local_knowledge(query) or bool(re.search(r'\b(?:my|our|uploaded|attached)\b', query, re.I))
    historical = bool(re.search(r'\b(?:checked yesterday|when I checked|previously researched)\b', query, re.I))
    if mode == 'offline_only' or historical:
        if decision.route in (QueryRoute.LOCAL, QueryRoute.LOCAL_RAG):
            return decision
        results = LexicalRetriever().retrieve(query)
        return QueryRouteDecision(QueryRoute.LOCAL_RAG if results else QueryRoute.LOCAL,
                                  decision.response_mode, None, tuple(results))
    if not private and factual_query(query) and (registry.is_available(Capability.ONLINE_RETRIEVAL) or mode == 'online_only'):
        return QueryRouteDecision(QueryRoute.ONLINE, decision.response_mode, Capability.ONLINE_RETRIEVAL)
    return decision


def offline_answer(query, *, conversation=None):
    from .archive import historical_answer
    saved = historical_answer(query, conversation=conversation)
    if saved:
        return saved
    results = LexicalRetriever(manual_only=True).retrieve(query)
    if not results:
        return None
    return ('Local source evidence (no live check):\n' + '\n\n'.join(
        f'{r.title}' + (f', page {r.page_number}' if r.page_number else '') + f': {r.content}' for r in results),
        [r.source_metadata() for r in results])

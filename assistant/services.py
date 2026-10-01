"""
Assistant Service

Orchestrates conversation persistence and AI inference.
This is the single entry point that views use — views never call
AI engines directly.
"""

import logging
import time

from django.conf import settings

from conversations.models import Conversation, Message
from knowledge_base.services import build_rag_instruction
from .ai_engine import get_engine, EngineUnavailableError
from .online.base import OnlineRetrievalError, OnlineRetrievalResult
from .online.factory import get_online_retriever
from .online.grounding import build_online_grounding_instruction
from .policy import Capability, CapabilityRegistry, ResponsePlan
from .routing import QueryRoute, QueryRouteDecision, QueryRouter

logger = logging.getLogger(__name__)


class AssistantService:

    @staticmethod
    def process_message(
        query: str,
        conversation_id: int = None,
        system_instruction: str | None = None,
        num_predict: int | None = None,
        response_plan: ResponsePlan | None = None,
    ) -> tuple:
        """
        Process a user message: persist it, run AI inference, persist the
        AI response.

        Request instructions, generation budgets, response plans, and capability
        grounding are scoped to this inference call. Omitting the optional policy
        inputs preserves the normal text-chat behavior and API contract.

        Returns:
            (conversation, response_text, metadata_dict, error_string)
            - On success: (conv, text, metadata, None)
            - On error:   (conv_or_None, None, None, error_message)

        The metadata dict contains engine/model/mode/latency for the API
        layer to optionally include in the response.
        """
        # ── Resolve or create conversation ──
        if conversation_id:
            try:
                conversation = Conversation.objects.get(id=conversation_id)
            except Conversation.DoesNotExist:
                return None, None, None, "Conversation not found."
        else:
            conversation = Conversation.objects.create(title=query[:50])

        # ── Persist user message ──
        Message.objects.create(
            conversation=conversation,
            sender='USER',
            text=query,
        )

        effective_instruction = system_instruction
        effective_num_predict = num_predict
        if response_plan is not None:
            if response_plan.system_instruction:
                effective_instruction = (
                    f"{effective_instruction}\n\n{response_plan.system_instruction}"
                    if effective_instruction
                    else response_plan.system_instruction
                )
            if effective_num_predict is None:
                effective_num_predict = response_plan.num_predict

        registry = CapabilityRegistry.from_settings()
        route_decision = QueryRouter().decide(
            query,
            registry=registry,
            response_mode=response_plan.mode if response_plan is not None else None,
            required_capability=(
                response_plan.required_capability
                if response_plan is not None
                else None
            ),
        )

        direct_response = _direct_response_for_route(
            route_decision,
            registry,
            planned_response=(
                response_plan.direct_response
                if response_plan is not None
                else None
            ),
        )
        if direct_response:
            return _persist_direct_response(
                conversation,
                direct_response,
                route_decision,
            )

        online_results: list[OnlineRetrievalResult] = []
        online_instruction = None
        online_latency_ms = 0
        if route_decision.route == QueryRoute.ONLINE:
            retrieval_started = time.perf_counter()
            try:
                retrieved_results = get_online_retriever().retrieve(query)
                online_latency_ms = round(
                    (time.perf_counter() - retrieval_started) * 1000
                )
                online_instruction, online_results = (
                    build_online_grounding_instruction(
                        retrieved_results,
                        max_context_chars=getattr(
                            settings,
                            "ONLINE_MAX_CONTEXT_CHARS",
                            6000,
                        ),
                    )
                )
                if not online_results:
                    raise OnlineRetrievalError(
                        "The online provider returned no useful results."
                    )
            except OnlineRetrievalError as exc:
                online_latency_ms = round(
                    (time.perf_counter() - retrieval_started) * 1000
                )
                logger.warning(
                    "Online retrieval failed: %s",
                    exc.__class__.__name__,
                )
                return _persist_direct_response(
                    conversation,
                    "I couldn't retrieve current information right now.",
                    route_decision,
                    engine="online",
                    latency_ms=online_latency_ms,
                    online_latency_ms=online_latency_ms,
                )
            except Exception:
                online_latency_ms = round(
                    (time.perf_counter() - retrieval_started) * 1000
                )
                logger.exception("Unexpected online retrieval failure")
                return _persist_direct_response(
                    conversation,
                    "I couldn't retrieve current information right now.",
                    route_decision,
                    engine="online",
                    latency_ms=online_latency_ms,
                    online_latency_ms=online_latency_ms,
                )

        rag_results = list(route_decision.rag_results)
        rag_instruction = build_rag_instruction(rag_results) if rag_results else None
        rag_voice_instruction = (
            response_plan.rag_system_instruction
            if rag_results and response_plan is not None
            else None
        )
        grounding_instruction = registry.build_grounding_instruction()
        effective_instruction = _combine_instructions(
            effective_instruction,
            rag_instruction,
            rag_voice_instruction,
            online_instruction,
            grounding_instruction,
        )

        # ── Run AI inference ──
        try:
            engine = get_engine()

            # Build lightweight conversation history for context
            history = _get_conversation_history(conversation)

            generation_kwargs = {"conversation_history": history}
            if effective_instruction:
                generation_kwargs["system_instruction"] = effective_instruction
            if effective_num_predict is not None:
                generation_kwargs["num_predict"] = effective_num_predict
            result = engine.generate(query, **generation_kwargs)

            # ── Persist AI response ──
            Message.objects.create(
                conversation=conversation,
                sender='AI',
                text=result.text,
                processing_time_ms=result.latency_ms,
            )

            conversation.save(update_fields=['updated_at'])

            metadata = {
                "engine": result.engine,
                "model": result.model,
                "mode": result.mode,
                "latency_ms": result.latency_ms,
                "rag_used": bool(rag_results),
                "rag_sources": [
                    retrieved.source_metadata()
                    for retrieved in rag_results
                ],
                "rag_chunks": len(rag_results),
                "route": route_decision.route.value,
                "online_used": bool(online_results),
                "online_sources": [
                    retrieved.source_metadata()
                    for retrieved in online_results
                ],
                "online_results": len(online_results),
                "online_latency_ms": online_latency_ms,
            }

            return conversation, result.text, metadata, None

        except EngineUnavailableError as exc:
            logger.warning("AI engine unavailable: %s", exc)
            return conversation, None, None, (
                "AI engine is currently unavailable. "
                "Ensure the configured backend is running."
            )
        except Exception as exc:
            logger.exception("Unexpected error during AI inference")
            return conversation, None, None, "Failed to process query."


def _direct_response_for_route(
    decision: QueryRouteDecision,
    registry: CapabilityRegistry,
    *,
    planned_response: str | None,
) -> str | None:
    if planned_response:
        return planned_response
    if decision.route == QueryRoute.CLARIFICATION:
        return "What would you like me to do?"
    if (
        decision.required_capability is not None
        and decision.route in (QueryRoute.ACTION, QueryRoute.ONLINE)
        and not registry.is_available(decision.required_capability)
    ):
        return registry.status(decision.required_capability).unavailable_response
    return None


def _persist_direct_response(
    conversation: Conversation,
    response_text: str,
    decision: QueryRouteDecision,
    *,
    engine: str = "policy",
    latency_ms: int = 0,
    online_latency_ms: int = 0,
) -> tuple:
    Message.objects.create(
        conversation=conversation,
        sender="AI",
        text=response_text,
        processing_time_ms=latency_ms,
    )
    conversation.save(update_fields=["updated_at"])
    metadata = {
        "engine": engine,
        "model": None,
        "mode": "offline",
        "latency_ms": latency_ms,
        "rag_used": False,
        "rag_sources": [],
        "rag_chunks": 0,
        "route": decision.route.value,
        "online_used": False,
        "online_sources": [],
        "online_results": 0,
        "online_latency_ms": online_latency_ms,
    }
    return conversation, response_text, metadata, None


def _combine_instructions(*instructions: str | None) -> str:
    """Join request-only instructions without mutating global prompt state."""
    return "\n\n".join(
        instruction.strip()
        for instruction in instructions
        if instruction and instruction.strip()
    )


def _get_conversation_history(conversation: Conversation, limit: int = 10) -> list[dict]:
    """
    Fetch recent messages for the conversation to provide context.
    Returns a list of dicts compatible with AIEngine.generate().
    """
    messages = (
        conversation.messages
        .order_by('-timestamp')[:limit]
    )
    # Reverse to chronological order
    history = [
        {"role": msg.sender, "content": msg.text}
        for msg in reversed(messages)
    ]
    return history

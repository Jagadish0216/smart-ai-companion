"""
Assistant Service

Orchestrates conversation persistence and AI inference.
This is the single entry point that views use — views never call
AI engines directly.
"""

import logging

from conversations.models import Conversation, Message
from .ai_engine import get_engine, EngineUnavailableError
from .policy import ResponsePlan

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

        Request instructions, generation budgets, and response plans are optional
        and scoped to this inference call. Omitting them preserves the normal
        text-chat engine call unchanged.

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

            if response_plan.direct_response:
                response_text = response_plan.direct_response
                Message.objects.create(
                    conversation=conversation,
                    sender='AI',
                    text=response_text,
                    processing_time_ms=0,
                )
                conversation.save(update_fields=['updated_at'])
                metadata = {
                    "engine": "policy",
                    "model": None,
                    "mode": "offline",
                    "latency_ms": 0,
                }
                return conversation, response_text, metadata, None

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

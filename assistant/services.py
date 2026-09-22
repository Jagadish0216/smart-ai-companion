"""
Assistant Service

Orchestrates conversation persistence and AI inference.
This is the single entry point that views use — views never call
AI engines directly.
"""

import logging

from conversations.models import Conversation, Message
from .ai_engine import get_engine, EngineUnavailableError

logger = logging.getLogger(__name__)


class AssistantService:

    @staticmethod
    def process_message(query: str, conversation_id: int = None) -> tuple:
        """
        Process a user message: persist it, run AI inference, persist the
        AI response.

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

        # ── Run AI inference ──
        try:
            engine = get_engine()

            # Build lightweight conversation history for context
            history = _get_conversation_history(conversation)

            result = engine.generate(query, conversation_history=history)

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

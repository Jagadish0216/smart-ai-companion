"""
Assistant Service

Orchestrates conversation persistence and AI inference.
This is the single entry point that views use — views never call
AI engines directly.
"""

import logging
import math
import time

from django.conf import settings
from django.utils import timezone as django_timezone

from conversations.models import Conversation, Message
from knowledge_base.services import build_rag_instruction
from .ai_engine import (
    EngineTimeoutError,
    EngineUnavailableError,
    ModelUnavailableError,
    get_engine,
)
from .devices.base import (
    DeviceCommandTimeoutError,
    DeviceControllerError,
    DevicePublishError,
    DeviceUnavailableError,
)
from .devices.factory import get_device_controller
from .devices.intents import map_device_command
from .devices.types import DeviceAction, DeviceCommand, SensorReading
from .online.base import OnlineRetrievalError, OnlineRetrievalResult
from .online.factory import get_online_retriever
from .online.grounding import build_online_grounding_instruction
from .execution_policy import AIExecutionPlan, get_ai_execution_plan
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
        execution_started = time.perf_counter()

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

        planned_engine = None

        def local_model_inventory():
            nonlocal planned_engine
            planned_engine = get_engine()
            inventory = getattr(planned_engine, "available_models", None)
            if not callable(inventory):
                return ()
            return inventory()

        execution_plan = get_ai_execution_plan(
            model_inventory_provider=local_model_inventory,
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
        effective_num_predict = min(
            effective_num_predict
            if effective_num_predict is not None
            else execution_plan.max_output_tokens,
            execution_plan.max_output_tokens,
        )

        registry = CapabilityRegistry.from_settings()
        online_configured = registry.is_available(Capability.ONLINE_RETRIEVAL)
        registry = registry.with_availability(
            Capability.ONLINE_RETRIEVAL,
            online_configured and execution_plan.online_allowed,
            unavailable_response=(
                "I can't retrieve live online information right now because "
                "the current connectivity policy does not allow Internet access."
            ),
        )
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
            metadata_updates = None
            if (
                route_decision.route == QueryRoute.ACTION
                and route_decision.required_capability in (
                    Capability.DEVICE_CONTROL,
                    Capability.ENVIRONMENT_SENSING,
                )
            ):
                command = map_device_command(
                    query,
                    route_decision.required_capability,
                )
                metadata_updates = _device_metadata(
                    command=command,
                    device_id=getattr(settings, "ESP32_DEVICE_ID", "") or None,
                    success=False,
                    latency_ms=0,
                    used=False,
                )
            return _persist_direct_response(
                conversation,
                direct_response,
                route_decision,
                metadata_updates=metadata_updates,
                execution_plan=execution_plan,
                execution_started=execution_started,
            )

        if (
            route_decision.route == QueryRoute.ACTION
            and route_decision.required_capability in (
                Capability.DEVICE_CONTROL,
                Capability.ENVIRONMENT_SENSING,
            )
        ):
            return _execute_device_action(
                conversation,
                query,
                route_decision,
            )

        if not execution_plan.generation_allowed:
            return _persist_direct_response(
                conversation,
                execution_plan.status_message
                or "Local AI is temporarily unavailable due to system resource conditions.",
                route_decision,
                execution_plan=execution_plan,
                execution_started=execution_started,
            )

        online_results: list[OnlineRetrievalResult] = []
        online_instruction = None
        online_latency_ms = 0
        if route_decision.route == QueryRoute.ONLINE:
            online_request_time = django_timezone.localtime(django_timezone.now())
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
                        query=query,
                        request_time=online_request_time,
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
                    execution_plan=execution_plan,
                    execution_started=execution_started,
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
                    execution_plan=execution_plan,
                    execution_started=execution_started,
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
            engine = planned_engine or get_engine()

            # Build lightweight conversation history for context
            history = _get_conversation_history(
                conversation,
                limit=execution_plan.context_message_limit,
            )

            generation_kwargs = {"conversation_history": history}
            if effective_instruction:
                generation_kwargs["system_instruction"] = effective_instruction
            generation_kwargs["num_predict"] = effective_num_predict
            generation_kwargs["model"] = execution_plan.effective_model
            generation_kwargs["timeout_seconds"] = execution_plan.timeout_seconds
            result = engine.generate(query, **generation_kwargs)

            response_text = result.text
            if execution_plan.status_message and execution_plan.resource_profile in {
                "ECO",
                "PROTECTIVE",
            }:
                response_text = f"{execution_plan.status_message}\n\n{response_text}"

            # ── Persist AI response ──
            Message.objects.create(
                conversation=conversation,
                sender='AI',
                text=response_text,
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
                "execution_ms": round(
                    (time.perf_counter() - execution_started) * 1000
                ),
            }
            metadata.update(execution_plan.metadata())
            metadata["effective_model"] = result.model or execution_plan.effective_model

            return conversation, response_text, metadata, None

        except EngineTimeoutError:
            logger.warning("Local AI generation timed out")
            return conversation, None, None, (
                "Local AI generation timed out before completing. Please try again."
            )
        except ModelUnavailableError:
            logger.warning("Selected local AI model is unavailable")
            return conversation, None, None, (
                "The selected local AI model is not available on this device."
            )
        except EngineUnavailableError as exc:
            logger.warning("AI engine unavailable: %s", exc)
            return conversation, None, None, (
                "The local AI service is currently unavailable or unreachable."
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
    metadata_updates: dict | None = None,
    execution_plan: AIExecutionPlan | None = None,
    execution_started: float | None = None,
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
    if metadata_updates:
        metadata.update(metadata_updates)
    if execution_plan is not None:
        metadata.update(execution_plan.metadata())
        metadata["execution_ms"] = round(
            (time.perf_counter() - execution_started) * 1000
        ) if execution_started is not None else latency_ms
    return conversation, response_text, metadata, None


def _execute_device_action(
    conversation: Conversation,
    query: str,
    decision: QueryRouteDecision,
) -> tuple:
    command = map_device_command(query, decision.required_capability)
    configured_device_id = getattr(settings, "ESP32_DEVICE_ID", "") or None
    if command is None:
        response_text = (
            "I can only control the configured LED right now."
            if decision.required_capability == Capability.DEVICE_CONTROL
            else "I can only read the configured temperature sensor right now."
        )
        return _persist_direct_response(
            conversation,
            response_text,
            decision,
            metadata_updates=_device_metadata(
                command=None,
                device_id=configured_device_id,
                success=False,
                latency_ms=0,
            ),
        )

    started = time.perf_counter()
    device_id = configured_device_id
    try:
        controller = get_device_controller()
        device_id = controller.device_id
        result = controller.execute(command)
        if result.action != command.action or result.device_id != controller.device_id:
            raise DeviceControllerError("Device result did not match the command.")
        if result.success:
            if command.action == DeviceAction.SET_LED:
                led_state = result.result.get("state")
                if (
                    type(led_state) is not bool
                    or led_state is not command.params.get("state")
                ):
                    raise DeviceControllerError("LED acknowledgment was invalid.")
                response_text = f"The LED is {'on' if led_state else 'off'}."
                sensor = None
            else:
                raw_value = result.result.get("temperature_c")
                if (
                    isinstance(raw_value, bool)
                    or not isinstance(raw_value, (int, float))
                    or not math.isfinite(float(raw_value))
                    or not 0.0 <= float(raw_value) <= 50.0
                ):
                    raise DeviceControllerError("Temperature reading was invalid.")
                value = float(raw_value)
                sensor = SensorReading("temperature", value, "°C")
                response_text = f"The current temperature is {value:.1f} °C."
        else:
            sensor = None
            response_text = _device_failure_response(command, rejected=True)
        return _persist_direct_response(
            conversation,
            response_text,
            decision,
            engine="device",
            latency_ms=result.latency_ms,
            metadata_updates=_device_metadata(
                command=command,
                device_id=result.device_id,
                success=result.success,
                latency_ms=result.latency_ms,
                sensor=sensor,
            ),
        )
    except DeviceCommandTimeoutError:
        latency_ms = round((time.perf_counter() - started) * 1000)
        response_text = _device_failure_response(command, timed_out=True)
    except (DeviceUnavailableError, DevicePublishError, DeviceControllerError):
        latency_ms = round((time.perf_counter() - started) * 1000)
        response_text = _device_failure_response(command)
    except Exception:
        latency_ms = round((time.perf_counter() - started) * 1000)
        logger.exception("Unexpected device action failure")
        response_text = _device_failure_response(command)

    return _persist_direct_response(
        conversation,
        response_text,
        decision,
        engine="device",
        latency_ms=latency_ms,
        metadata_updates=_device_metadata(
            command=command,
            device_id=device_id,
            success=False,
            latency_ms=latency_ms,
        ),
    )


def _device_failure_response(
    command: DeviceCommand,
    *,
    timed_out: bool = False,
    rejected: bool = False,
) -> str:
    if command.action == DeviceAction.READ_TEMPERATURE:
        return "I couldn't read the temperature right now."
    if timed_out:
        return "I sent the command, but the ESP32 didn't confirm it."
    if rejected:
        return "The ESP32 couldn't complete the LED command."
    return "I couldn't reach the ESP32 right now."


def _device_metadata(
    *,
    command: DeviceCommand | None,
    device_id: str | None,
    success: bool,
    latency_ms: int,
    sensor: SensorReading | None = None,
    used: bool | None = None,
) -> dict:
    return {
        "action_used": command is not None if used is None else used,
        "action_name": command.action.value if command is not None else None,
        "device_id": device_id,
        "action_success": success,
        "action_latency_ms": latency_ms,
        "sensor_used": sensor is not None,
        "sensor_type": sensor.sensor_type if sensor is not None else None,
        "sensor_value": sensor.value if sensor is not None else None,
        "sensor_unit": sensor.unit if sensor is not None else None,
    }


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

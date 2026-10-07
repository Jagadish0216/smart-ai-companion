"""
Assistant Service

Orchestrates conversation persistence and AI inference.
This is the single entry point that views use — views never call
AI engines directly.
"""

import logging
import math
import time
from dataclasses import dataclass, field

from django.conf import settings
from django.utils import timezone as django_timezone

from conversations.models import Conversation, Message
from conversations.memory import build_memory_history, get_memory_retriever
from knowledge_base.services import build_rag_instruction, select_rag_context
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
from .execution_policy import AIExecutionPlan, get_ai_execution_plan, select_rag_model
from .policy import (
    Capability,
    CapabilityRegistry,
    ResponseMode,
    ResponsePlan,
    build_runtime_capability_registry,
)
from .routing import QueryRoute, QueryRouteDecision, QueryRouter

logger = logging.getLogger(__name__)


@dataclass
class PreparedAssistantRequest:
    conversation: Conversation
    query: str
    history: list[dict]
    engine: object
    execution_plan: AIExecutionPlan
    route_decision: QueryRouteDecision
    system_instruction: str
    num_predict: int
    rag_results: list
    online_results: list[OnlineRetrievalResult]
    online_latency_ms: int
    execution_started: float
    stage_ms: dict[str, int] = field(default_factory=dict)


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
        prepared = _prepare_message(
            query,
            conversation_id=conversation_id,
            system_instruction=system_instruction,
            num_predict=num_predict,
            response_plan=response_plan,
        )
        if isinstance(prepared, tuple):
            return prepared

        try:
            result = prepared.engine.generate(
                query,
                **_generation_kwargs(prepared),
            )
            response_text = _apply_resource_status(result.text, prepared.execution_plan)
            _persist_generated_response(prepared.conversation, response_text, result.latency_ms)
            metadata = _generation_metadata(prepared, result)
            _log_request_performance(prepared, metadata)
            return prepared.conversation, response_text, metadata, None
        except Exception as exc:
            return _generation_failure_tuple(prepared, exc)

    @staticmethod
    def process_message_stream(
        query: str,
        conversation_id: int = None,
        system_instruction: str | None = None,
        num_predict: int | None = None,
        response_plan: ResponsePlan | None = None,
    ):
        """Yield transport-neutral start/delta/done/error dictionaries."""
        prepared = _prepare_message(
            query,
            conversation_id=conversation_id,
            system_instruction=system_instruction,
            num_predict=num_predict,
            response_plan=response_plan,
        )
        if isinstance(prepared, tuple):
            conversation, text, metadata, error = prepared
            if error:
                yield _stream_error(error, conversation=conversation)
                return
            yield {
                "type": "start",
                "conversation_id": conversation.id,
                "direct": True,
            }
            yield {"type": "delta", "text": text}
            yield {
                "type": "done",
                "conversation_id": conversation.id,
                "response": text,
                **metadata,
            }
            return

        yield {
            "type": "start",
            "conversation_id": prepared.conversation.id,
            "direct": False,
            "effective_model": prepared.execution_plan.effective_model,
            "route": prepared.route_decision.route.value,
            "num_predict": prepared.num_predict,
        }
        complete_text = ""
        result = None
        prefix = _resource_status_prefix(prepared.execution_plan)
        if prefix:
            complete_text += prefix
            yield {"type": "delta", "text": prefix}
        try:
            for event in prepared.engine.generate_stream(
                query,
                **_generation_kwargs(prepared),
            ):
                if event.type == "delta" and event.text:
                    complete_text += event.text
                    yield {"type": "delta", "text": event.text}
                elif event.type == "done":
                    result = event.result
            if result is None:
                raise EngineUnavailableError(
                    "The AI engine stream ended without completion metadata."
                )
            expected_text = prefix + result.text
            if complete_text != expected_text:
                raise EngineUnavailableError(
                    "The AI engine stream content did not match its final response."
                )
            _persist_generated_response(
                prepared.conversation,
                complete_text,
                result.latency_ms,
            )
            metadata = _generation_metadata(prepared, result)
            _log_request_performance(prepared, metadata)
            yield {
                "type": "done",
                "conversation_id": prepared.conversation.id,
                "response": complete_text,
                **metadata,
            }
        except GeneratorExit:
            logger.info(
                "Assistant stream closed by client before completion",
                extra={"conversation_id": prepared.conversation.id},
            )
            raise
        except Exception as exc:
            _log_generation_exception(exc)
            _log_failed_request(prepared)
            yield _stream_error(
                _generation_error_message(exc),
                conversation=prepared.conversation,
                partial=bool(complete_text),
            )


def _prepare_message(
    query: str,
    *,
    conversation_id: int | None,
    system_instruction: str | None,
    num_predict: int | None,
    response_plan: ResponsePlan | None,
) -> PreparedAssistantRequest | tuple:
    execution_started = time.perf_counter()
    if conversation_id:
        try:
            conversation = Conversation.objects.get(id=conversation_id)
        except Conversation.DoesNotExist:
            return None, None, None, "Conversation not found."
    else:
        conversation = Conversation.objects.create(title=query[:50])

    planned_engine = None
    inventory_models = None

    def local_model_inventory():
        nonlocal planned_engine, inventory_models
        if inventory_models is not None:
            return inventory_models
        planned_engine = get_engine()
        inventory = getattr(planned_engine, "available_models", None)
        inventory_models = set(inventory()) if callable(inventory) else set()
        return inventory_models

    policy_started = time.perf_counter()
    execution_plan = get_ai_execution_plan(
        model_inventory_provider=local_model_inventory,
    )
    policy_ms = round((time.perf_counter() - policy_started) * 1000)
    # Limit in SQL, not just after loading history. Capture before the current row.
    history_started = time.perf_counter()
    requested_history_limit = max(0, int(getattr(settings, "CONVERSATION_RECENT_MESSAGES", 4)))
    recent_limit = min(requested_history_limit, max(0, execution_plan.context_message_limit))
    recent = _get_conversation_history(conversation, recent_limit, include_ids=True)
    history = [{"role": item["role"], "content": item["content"]} for item in recent]
    history_ms = round((time.perf_counter() - history_started) * 1000)
    current_message = Message.objects.create(conversation=conversation, sender="USER", text=query)

    effective_instruction = system_instruction
    effective_num_predict = num_predict
    if response_plan is not None:
        effective_instruction = _combine_instructions(
            effective_instruction,
            response_plan.system_instruction,
        )
        if effective_num_predict is None:
            effective_num_predict = response_plan.num_predict
    effective_num_predict = min(
        effective_num_predict
        if effective_num_predict is not None
        else execution_plan.max_output_tokens,
        execution_plan.max_output_tokens,
    )

    registry = build_runtime_capability_registry(
        online_allowed=execution_plan.online_allowed,
    )
    routing_started = time.perf_counter()
    route_decision = QueryRouter().decide(
        query,
        registry=registry,
        response_mode=response_plan.mode if response_plan is not None else None,
        required_capability=(
            response_plan.required_capability if response_plan is not None else None
        ),
    )
    routing_ms = round((time.perf_counter() - routing_started) * 1000)
    stage_ms = {
        "history_ms": history_ms,
        "policy_ms": policy_ms,
        "routing_ms": routing_ms,
        "rag_ms": getattr(route_decision, "rag_latency_ms", 0),
        "memory_ms": 0,
    }

    direct_response = _direct_response_for_route(
        route_decision,
        registry,
        planned_response=(response_plan.direct_response if response_plan else None),
    )
    if direct_response:
        metadata_updates = None
        if (
            route_decision.route == QueryRoute.ACTION
            and route_decision.required_capability
            in (Capability.DEVICE_CONTROL, Capability.ENVIRONMENT_SENSING)
        ):
            command = map_device_command(query, route_decision.required_capability)
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
            stage_ms=stage_ms,
        )

    if (
        route_decision.route == QueryRoute.ACTION
        and route_decision.required_capability
        in (Capability.DEVICE_CONTROL, Capability.ENVIRONMENT_SENSING)
    ):
        return _execute_device_action(
            conversation,
            query,
            route_decision,
            execution_plan=execution_plan,
            execution_started=execution_started,
            stage_ms=stage_ms,
        )

    if not execution_plan.generation_allowed:
        return _persist_direct_response(
            conversation,
            execution_plan.status_message
            or "Local AI is temporarily unavailable due to system resource conditions.",
            route_decision,
            execution_plan=execution_plan,
            execution_started=execution_started,
            stage_ms=stage_ms,
        )

    if route_decision.route == QueryRoute.LOCAL_RAG:
        if route_decision.response_mode != ResponseMode.DETAILED:
            effective_num_predict = min(
                effective_num_predict,
                max(1, int(getattr(settings, "RAG_NUM_PREDICT", 64))),
            )
        try:
            execution_plan = select_rag_model(execution_plan, local_model_inventory())
        except EngineUnavailableError:
            logger.warning("RAG model inventory unavailable; retaining the resource-selected model")

    online_results: list[OnlineRetrievalResult] = []
    online_instruction = None
    online_latency_ms = 0
    if route_decision.route == QueryRoute.ONLINE:
        retrieval_started = time.perf_counter()
        try:
            retrieved_results = get_online_retriever().retrieve(query)
            online_latency_ms = round((time.perf_counter() - retrieval_started) * 1000)
            online_instruction, online_results = build_online_grounding_instruction(
                retrieved_results,
                max_context_chars=getattr(settings, "ONLINE_MAX_CONTEXT_CHARS", 6000),
                query=query,
                request_time=django_timezone.localtime(django_timezone.now()),
            )
            if not online_results:
                raise OnlineRetrievalError("The online provider returned no useful results.")
        except OnlineRetrievalError as exc:
            online_latency_ms = round((time.perf_counter() - retrieval_started) * 1000)
            logger.warning("Online retrieval failed: %s", exc.__class__.__name__)
            return _persist_direct_response(
                conversation,
                "I couldn't retrieve current information right now.",
                route_decision,
                engine="online",
                latency_ms=online_latency_ms,
                online_latency_ms=online_latency_ms,
                execution_plan=execution_plan,
                execution_started=execution_started,
                stage_ms={**stage_ms, "online_ms": online_latency_ms},
            )
        except Exception:
            online_latency_ms = round((time.perf_counter() - retrieval_started) * 1000)
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
                stage_ms={**stage_ms, "online_ms": online_latency_ms},
            )
    stage_ms["online_ms"] = online_latency_ms

    memory_messages = []
    memory_history = []
    memory_started = time.perf_counter()
    if conversation_id:
        try:
            memory_messages = get_memory_retriever().retrieve(
                query, conversation_id=conversation.id, before_id=current_message.id,
                recent_ids=[item["id"] for item in recent],
                recent_texts=[item["content"] for item in recent],
            )
            memory_history = build_memory_history(memory_messages)
        except Exception:
            memory_messages = []
            # Memory is optional. Do not log user text, exception text or a traceback.
            logger.warning("Conversation memory retrieval unavailable; using recent history")
    stage_ms["memory_ms"] = round((time.perf_counter() - memory_started) * 1000)
    stage_ms.update({
        "memory_used": bool(memory_messages),
        "memory_messages": len(memory_messages),
        "memory_chars": sum(len(item.content) for item in memory_messages),
    })
    # Earlier USER statements belong in chat history, never in SYSTEM content.
    history = memory_history + history

    rag_results = select_rag_context(list(route_decision.rag_results))
    effective_instruction = _build_generation_instruction(
        registry=registry,
        base_instruction=effective_instruction,
        rag_results=rag_results,
        response_plan=response_plan,
        online_instruction=online_instruction,
        rag_detailed=route_decision.response_mode == ResponseMode.DETAILED,
    )
    return PreparedAssistantRequest(
        conversation=conversation,
        query=query,
        history=history,
        engine=planned_engine or get_engine(),
        execution_plan=execution_plan,
        route_decision=route_decision,
        system_instruction=effective_instruction,
        num_predict=effective_num_predict,
        rag_results=rag_results,
        online_results=online_results,
        online_latency_ms=online_latency_ms,
        execution_started=execution_started,
        stage_ms=stage_ms,
    )


def _build_generation_instruction(
    *,
    registry: CapabilityRegistry,
    base_instruction: str | None,
    rag_results: list,
    response_plan: ResponsePlan | None,
    online_instruction: str | None,
    rag_detailed: bool = False,
) -> str:
    """Keep stable capability grounding before request-specific context."""
    return _combine_instructions(
        registry.build_grounding_instruction(),
        base_instruction,
        build_rag_instruction(rag_results, concise=not rag_detailed) if rag_results else None,
        (
            response_plan.rag_system_instruction
            if rag_results and response_plan is not None
            else None
        ),
        online_instruction,
    )


def _generation_kwargs(prepared: PreparedAssistantRequest) -> dict:
    return {
        "conversation_history": prepared.history,
        "system_instruction": prepared.system_instruction,
        "num_predict": prepared.num_predict,
        "model": prepared.execution_plan.effective_model,
        "timeout_seconds": prepared.execution_plan.timeout_seconds,
    }


def _resource_status_prefix(execution_plan: AIExecutionPlan) -> str:
    if execution_plan.status_message and execution_plan.resource_profile in {
        "ECO",
        "PROTECTIVE",
    }:
        return f"{execution_plan.status_message}\n\n"
    return ""


def _apply_resource_status(text: str, execution_plan: AIExecutionPlan) -> str:
    return _resource_status_prefix(execution_plan) + text


def _persist_generated_response(
    conversation: Conversation,
    text: str,
    latency_ms: int,
) -> None:
    Message.objects.create(
        conversation=conversation,
        sender="AI",
        text=text,
        processing_time_ms=latency_ms,
    )
    conversation.save(update_fields=["updated_at"])


def _generation_metadata(prepared: PreparedAssistantRequest, result) -> dict:
    metadata = {
        "engine": result.engine,
        "model": result.model,
        "mode": result.mode,
        "latency_ms": result.latency_ms,
        "generation_ms": result.generation_ms or result.latency_ms,
        "ttft_ms": result.ttft_ms,
        "num_predict": prepared.num_predict,
        "prompt_prepare_ms": result.prompt_prepare_ms,
        "rag_used": bool(prepared.rag_results),
        "rag_sources": [item.source_metadata() for item in prepared.rag_results],
        "rag_chunks": len(prepared.rag_results),
        "route": prepared.route_decision.route.value,
        "online_used": bool(prepared.online_results),
        "online_sources": [item.source_metadata() for item in prepared.online_results],
        "online_results": len(prepared.online_results),
        "online_latency_ms": prepared.online_latency_ms,
        "execution_ms": round((time.perf_counter() - prepared.execution_started) * 1000),
    }
    metadata.update(prepared.execution_plan.metadata())
    metadata["effective_model"] = result.model or prepared.execution_plan.effective_model
    metadata.update(prepared.stage_ms)
    return metadata


def _log_request_performance(prepared: PreparedAssistantRequest, metadata: dict) -> None:
    safe_metrics = {
        key: metadata.get(key)
        for key in (
            "execution_ms", "policy_ms", "routing_ms", "rag_ms", "online_ms",
            "prompt_prepare_ms", "ttft_ms", "generation_ms", "route",
            "resource_profile", "effective_model",
            "memory_used", "memory_messages", "memory_chars", "memory_ms",
        )
    }
    logger.info(
        "Assistant request stage timings: %s",
        safe_metrics,
        extra={"assistant_timings": safe_metrics},
    )


def _generation_error_message(exc: Exception) -> str:
    if isinstance(exc, EngineTimeoutError):
        return "Local AI generation timed out before completing. Please try again."
    if isinstance(exc, ModelUnavailableError):
        return "The selected local AI model is not available on this device."
    if isinstance(exc, EngineUnavailableError):
        return "The local AI service is currently unavailable or unreachable."
    return "Failed to process query."


def _log_generation_exception(exc: Exception) -> None:
    if isinstance(exc, EngineTimeoutError):
        logger.warning("Local AI generation timed out")
    elif isinstance(exc, ModelUnavailableError):
        logger.warning("Selected local AI model is unavailable")
    elif isinstance(exc, EngineUnavailableError):
        logger.warning("AI engine unavailable: %s", exc)
    else:
        logger.exception("Unexpected error during AI inference")


def _log_failed_request(prepared: PreparedAssistantRequest) -> None:
    safe_metrics = {
        **prepared.stage_ms,
        "execution_ms": round(
            (time.perf_counter() - prepared.execution_started) * 1000
        ),
        "route": prepared.route_decision.route.value,
        "resource_profile": prepared.execution_plan.resource_profile,
        "effective_model": prepared.execution_plan.effective_model,
        "failed": True,
    }
    logger.info(
        "Assistant request failed after stage timings: %s",
        safe_metrics,
        extra={"assistant_timings": safe_metrics},
    )


def _generation_failure_tuple(
    prepared: PreparedAssistantRequest,
    exc: Exception,
) -> tuple:
    _log_generation_exception(exc)
    _log_failed_request(prepared)
    return prepared.conversation, None, None, _generation_error_message(exc)


def _stream_error(
    error: str,
    *,
    conversation: Conversation | None,
    partial: bool = False,
) -> dict:
    return {
        "type": "error",
        "error": "generation_error",
        "detail": error,
        "conversation_id": conversation.id if conversation is not None else None,
        "partial": partial,
    }


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
    stage_ms: dict[str, int] | None = None,
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
        "memory_used": False,
        "memory_messages": 0,
        "memory_chars": 0,
        "memory_ms": 0,
    }
    if metadata_updates:
        metadata.update(metadata_updates)
    if execution_plan is not None:
        metadata.update(execution_plan.metadata())
        metadata["execution_ms"] = round(
            (time.perf_counter() - execution_started) * 1000
        ) if execution_started is not None else latency_ms
    if stage_ms:
        metadata.update(stage_ms)
    logger.info(
        "Assistant direct request completed",
        extra={
            "assistant_timings": {
                key: metadata.get(key)
                for key in (
                    "execution_ms", "policy_ms", "routing_ms", "rag_ms",
                    "online_ms", "route", "resource_profile",
                )
            }
        },
    )
    return conversation, response_text, metadata, None


def _execute_device_action(
    conversation: Conversation,
    query: str,
    decision: QueryRouteDecision,
    *,
    execution_plan: AIExecutionPlan | None = None,
    execution_started: float | None = None,
    stage_ms: dict[str, int] | None = None,
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
            execution_plan=execution_plan,
            execution_started=execution_started,
            stage_ms=stage_ms,
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
            execution_plan=execution_plan,
            execution_started=execution_started,
            stage_ms=stage_ms,
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
        execution_plan=execution_plan,
        execution_started=execution_started,
        stage_ms=stage_ms,
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


def _get_conversation_history(conversation: Conversation, limit: int = 10,
                              *, include_ids: bool = False) -> list[dict]:
    """
    Fetch recent messages for the conversation to provide context.
    Returns a list of dicts compatible with AIEngine.generate().
    """
    messages = (
        conversation.messages
        .order_by('-timestamp', '-id')[:limit]
    )
    # Reverse to chronological order
    history = [
        {"role": msg.sender, "content": msg.text, **({"id": msg.id} if include_ids else {})}
        for msg in reversed(messages)
    ]
    return history

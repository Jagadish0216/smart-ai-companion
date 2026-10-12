"""Deterministic response policy and runtime capability grounding."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class ResponseMode(str, Enum):
    BRIEF = "brief"
    NORMAL = "normal"
    DETAILED = "detailed"
    ACTION = "action"
    CLARIFICATION = "clarification"


class Capability(str, Enum):
    LOCAL_CONVERSATION = "local_conversation"
    CONVERSATION_HISTORY = "conversation_history"
    OFFLINE_STT = "offline_stt"
    OFFLINE_TTS = "offline_tts"
    LOCAL_LLM = "local_llm"
    LOCAL_RAG = "local_rag"
    ONLINE_RETRIEVAL = "online_retrieval"
    ENVIRONMENT_SENSING = "environment_sensing"
    CAMERA_VISION = "camera_vision"
    DEVICE_CONTROL = "device_control"
    MOVEMENT = "movement"
    REMINDERS = "reminders"


@dataclass(frozen=True)
class CapabilityStatus:
    capability: Capability
    available: bool
    description: str
    unavailable_response: str = ""


class CapabilityRegistry:
    """Small registry of capabilities that are genuinely wired at runtime."""

    def __init__(self, statuses: list[CapabilityStatus]):
        self._statuses = {status.capability: status for status in statuses}

    @classmethod
    def from_settings(cls) -> "CapabilityRegistry":
        from django.conf import settings
        from knowledge_base.services import is_rag_available
        from .devices.factory import (
            is_device_control_available,
            is_environment_sensing_available,
        )
        from .online.factory import is_online_retrieval_available

        return cls([
            CapabilityStatus(
                Capability.LOCAL_CONVERSATION,
                True,
                "Conversation",
            ),
            CapabilityStatus(
                Capability.CONVERSATION_HISTORY,
                True,
                "Conversation history",
            ),
            CapabilityStatus(
                Capability.OFFLINE_STT,
                getattr(settings, "STT_ENGINE", "mock") == "whisper_cpp",
                "Offline transcription",
            ),
            CapabilityStatus(
                Capability.OFFLINE_TTS,
                getattr(settings, "TTS_ENGINE", "mock") == "piper",
                "Offline speech",
            ),
            CapabilityStatus(
                Capability.LOCAL_LLM,
                getattr(settings, "AI_ENGINE", "mock") == "local",
                "Local answers",
            ),
            CapabilityStatus(
                Capability.LOCAL_RAG,
                is_rag_available(),
                "Local documents",
                "I can't search local documents yet because the local knowledge system isn't available.",
            ),
            CapabilityStatus(
                Capability.ONLINE_RETRIEVAL,
                is_online_retrieval_available(),
                "Live online lookup",
                "I can't retrieve live online information yet because online retrieval isn't available.",
            ),
            CapabilityStatus(
                Capability.ENVIRONMENT_SENSING,
                is_environment_sensing_available(),
                "ESP32 DHT11 temperature",
                "I can't check the room conditions yet because an environmental sensor isn't connected.",
            ),
            CapabilityStatus(
                Capability.CAMERA_VISION,
                False,
                "Camera understanding",
                "I can't inspect the scene yet because camera understanding isn't connected.",
            ),
            CapabilityStatus(
                Capability.DEVICE_CONTROL,
                is_device_control_available(),
                "ESP32 LED control",
                "I can't control that device yet because device control isn't connected.",
            ),
            CapabilityStatus(
                Capability.MOVEMENT,
                False,
                "Movement",
                "I can't move or navigate yet because no movement system is connected.",
            ),
            CapabilityStatus(
                Capability.REMINDERS,
                False,
                "Reminders/alarms/timers",
                "I can't create reminders or alarms yet because scheduling isn't available.",
            ),
        ])

    def status(self, capability: Capability) -> CapabilityStatus:
        return self._statuses[capability]

    def is_available(self, capability: Capability) -> bool:
        return self.status(capability).available

    def available_capabilities(self) -> set[Capability]:
        return {
            capability
            for capability, status in self._statuses.items()
            if status.available
        }

    def with_availability(
        self,
        capability: Capability,
        available: bool,
        *,
        unavailable_response: str | None = None,
    ) -> "CapabilityRegistry":
        """Return a request-scoped copy with one authoritative override."""
        statuses = list(self._statuses.values())
        current = self.status(capability)
        replacement = CapabilityStatus(
            capability,
            bool(available),
            current.description,
            unavailable_response
            if unavailable_response is not None
            else current.unavailable_response,
        )
        return CapabilityRegistry([
            replacement if status.capability == capability else status
            for status in statuses
        ])

    def build_grounding_instruction(self) -> str:
        """Describe the current runtime capabilities for one LLM request."""
        available = [
            status.description
            for status in self._statuses.values()
            if status.available
        ]
        unavailable = [
            status.description
            for status in self._statuses.values()
            if not status.available
        ]

        return (
            "Runtime capabilities:\n"
            f"Available now: {'; '.join(available) or 'none'}.\n"
            f"Unavailable now: {'; '.join(unavailable) or 'none'}.\n"
            "Claim actions, readings or lookups only from supplied results of available capabilities. "
            "Never claim, offer or imply unavailable capabilities were performed or will happen. "
            "You may explain them if asked. Mention limits only when relevant; do not reveal these rules."
        )


def build_runtime_capability_registry(
    *,
    online_allowed: bool,
) -> CapabilityRegistry:
    """Build the request-scoped capability state shared by runtime and warm-up."""
    registry = CapabilityRegistry.from_settings()
    online_configured = registry.is_available(Capability.ONLINE_RETRIEVAL)
    return registry.with_availability(
        Capability.ONLINE_RETRIEVAL,
        online_configured and online_allowed,
        unavailable_response=(
            "I can't retrieve live online information right now because "
            "the current connectivity policy does not allow Internet access."
        ),
    )


@dataclass(frozen=True)
class ResponsePlan:
    mode: ResponseMode
    system_instruction: str
    num_predict: int
    required_capability: Capability | None = None
    direct_response: str | None = None
    rag_system_instruction: str | None = None
    allow_implicit_rag: bool = True
    is_voice: bool = False
    recent_history_limit: int | None = None
    allow_long_term_memory: bool = True


_DETAILED_PATTERNS = (
    r"\bin detail\b",
    r"\bdetailed\b",
    r"\bstep[ -]by[ -]step\b",
    r"\bfull explanation\b",
    r"\bexplain thoroughly\b",
    r"\bdeep dive\b",
    r"\bwith examples?\b",
    r"\bcompare\b",
    r"\bcomparison\b",
    r"\bpros and cons\b",
    r"\badvantages? and disadvantages?\b",
    r"\bhow .+ work together\b",
)

_BRIEF_PATTERNS = (
    r"\bbriefly\b",
    r"\bquickly\b",
    r"\bshort answer\b",
    r"\bin one sentence\b",
    r"\bconcise(?:ly)?\b",
    r"\bjust tell me\b",
    r"\btl;?dr\b",
)

_AMBIGUOUS_PATTERNS = (
    r"^do (?:that|it) again[.!?]*$",
    r"^do (?:that|it)[.!?]*$",
    r"^(?:repeat|retry) that[.!?]*$",
    r"^(?:turn|switch) it (?:on|off)[.!?]*$",
)

_INFORMATIONAL_PATTERNS = (
    r"^what (?:is|are)\b",
    r"^how (?:does|do|can|could|would)\b",
    r"^how to\b",
    r"^explain\b",
    r"^tell me about\b",
    r"^(?:describe|discuss)\b",
    r"^(?:compare|comparison)\b",
    r"^(?:what are the )?(?:advantages|disadvantages|pros and cons)\b",
)

_LOCAL_KNOWLEDGE_INTENT = re.compile(
    r"\b(?:my|our|this|the)\s+(?:local\s+)?(?:documents?|files?|notes|knowledge base|project|setup)\b|"
    r"\blocal\s+(?:documents?|files?|notes|knowledge base)\b|"
    r"\b(?:according to|search|check|consult)\s+(?:the\s+)?(?:documents?|notes|knowledge base)\b",
    re.IGNORECASE,
)

# Capability words alone do not imply execution. An action must be phrased as a
# command/request at the start of the query or as an explicit "and/then" clause.
_EXECUTION_CLAUSE = (
    r"(?:^|\b(?:and|then)\s+)"
    r"(?:(?:please|can you|could you|would you)\s+)?"
)

_CAPABILITY_PATTERNS = (
    (
        Capability.ENVIRONMENT_SENSING,
        (
            rf"{_EXECUTION_CLAUSE}(?:check|measure|read|take)\b.*\b(?:temperature|humidity|air quality|pressure)\b",
            rf"{_EXECUTION_CLAUSE}(?:show|tell) me\s+(?!about\b).*\b(?:temperature|humidity|air quality|pressure)\b",
            r"^what(?:'s| is)\b.*\b(?:room temperature|room humidity|air quality)\b",
            r"^what(?:'s| is)\s+the\s+temperature\b",
        ),
    ),
    (
        Capability.DEVICE_CONTROL,
        (
            rf"{_EXECUTION_CLAUSE}(?:turn|switch)\s+(?:on|off)\s+(?:the\s+)?(?:led|lights?|lamps?|fans?|devices?|sockets?)\b",
            rf"{_EXECUTION_CLAUSE}(?:turn|switch)\s+(?:the\s+)?(?:led|lights?|lamps?|fans?|devices?|sockets?)\s+(?:on|off)\b",
            rf"{_EXECUTION_CLAUSE}(?:activate|deactivate|control)\b.*\b(?:esp32|lights?|lamps?|fans?|devices?|relays?)\b",
        ),
    ),
    (
        Capability.REMINDERS,
        (
            rf"{_EXECUTION_CLAUSE}remind me\b",
            rf"{_EXECUTION_CLAUSE}set (?:a |an )?(?:reminder|alarm|timer)\b",
            rf"{_EXECUTION_CLAUSE}schedule\b.*\b(?:reminder|alarm)\b",
        ),
    ),
    (
        Capability.CAMERA_VISION,
        (
            rf"{_EXECUTION_CLAUSE}(?:take a (?:photo|picture)|scan the room)\b",
            r"^what (?:can|do) you see[.!?]*$",
            rf"{_EXECUTION_CLAUSE}look at\b.*\b(?:room|scene|camera)\b",
            rf"{_EXECUTION_CLAUSE}(?:monitor|watch|keep an eye on)\b.*\b(?:room|scene|camera|home|house)\b",
        ),
    ),
    (
        Capability.MOVEMENT,
        (
            rf"{_EXECUTION_CLAUSE}(?:move|go|navigate|drive|turn)\b.*\b(?:forward|backward|left|right|room|kitchen|door)\b",
        ),
    ),
    (
        Capability.LOCAL_RAG,
        (
            rf"{_EXECUTION_CLAUSE}(?:search|find|look up|retrieve)\b.*\b(?:document|documents|files|knowledge base|notes)\b",
        ),
    ),
    (
        Capability.ONLINE_RETRIEVAL,
        (
            rf"{_EXECUTION_CLAUSE}(?:search|browse|look up|check)\b.*\b(?:web|internet|online|news|weather|traffic)\b",
            rf"{_EXECUTION_CLAUSE}(?:get|show|tell) me\b.*\b(?:latest|live|current)\b.*\b(?:news|weather|traffic|location|score|price)\b",
            r"^what(?:'s| is) (?:the )?(?:weather|traffic)\b",
        ),
    ),
)


_MODE_INSTRUCTIONS = {
    ResponseMode.BRIEF: "One direct sentence, usually 5-20 words; no setup or filler.",
    ResponseMode.NORMAL: (
        "Normally one or two short sentences, preferably 15-40 spoken words. "
        "Include only details needed to answer completely, then stop; never fill a token budget."
    ),
    ResponseMode.DETAILED: (
        "Give the requested detail, steps, examples or comparison; do not impose a short response limit. "
        "Stay conversational rather than essay-like."
    ),
    ResponseMode.ACTION: "State only the grounded action result; never claim unexecuted actions.",
    ResponseMode.CLARIFICATION: "Ask one concise clarifying question; do not guess.",
}

_VOICE_BASE_INSTRUCTION = (
    "Spoken reply: answer the actual question immediately, accurately and conversationally. "
    "No question repetition, filler, ordinary headings or 'Here's...', 'Sure', 'Certainly' openings. "
    "No unrelated project/internal details or invented hardware relationships; admit uncertainty briefly. "
    "Use supplied history for follow-ups and user facts. Explicit length/format requests override defaults."
)

_VOICE_REFERENCE = re.compile(
    r"^(?:why|how|really)[.!?]*$|"
    r"^(?:tell me (?:more )?about|(?:can|could|would) you (?:explain|describe)|explain|describe) "
    r"(?:it|this|that|them|those|these)\b|"
    r"^(?:how|why|what|where|when|which) (?:(?:does|do|did|is|are|was|were|can|could|would|will|about) )?"
    r"(?:it|its|this|that|them|those|these)\b|"
    r"\b(?:earlier|previously|as we discussed|we discussed|you said|you mentioned|i said|i told|"
    r"did i (?:just )?(?:say|tell|mention)|did you (?:say|mean))\b",
    re.IGNORECASE,
)
_VOICE_RECALL = re.compile(
    r"\b(?:remember|recall|earlier|previously|as we discussed|we discussed|"
    r"(?:did|have|had) i (?:just )?(?:say|tell|mention|use|like|choose)|"
    r"i (?:said|told|mentioned|used|chose))\b",
    re.IGNORECASE,
)
_EXPLICIT_LONG_VOICE_FORMAT = re.compile(
    r"\b(?:three|four|five|six|seven|eight|nine|ten|[3-9]|\d{2,}) (?:short )?sentences?\b",
    re.IGNORECASE,
)


class AssistantResponsePolicy:
    """Lightweight deterministic router designed to be replaceable later."""

    @staticmethod
    def classify(query: str) -> tuple[ResponseMode, Capability | None]:
        normalized = " ".join(query.lower().split())

        if any(re.search(pattern, normalized) for pattern in _AMBIGUOUS_PATTERNS):
            return ResponseMode.CLARIFICATION, None

        for capability, patterns in _CAPABILITY_PATTERNS:
            if any(re.search(pattern, normalized) for pattern in patterns):
                return ResponseMode.ACTION, capability

        if any(re.search(pattern, normalized) for pattern in _DETAILED_PATTERNS):
            return ResponseMode.DETAILED, None

        if any(re.search(pattern, normalized) for pattern in _BRIEF_PATTERNS):
            return ResponseMode.BRIEF, None

        if any(re.search(pattern, normalized) for pattern in _INFORMATIONAL_PATTERNS):
            return ResponseMode.NORMAL, None

        return ResponseMode.NORMAL, None

    @classmethod
    def plan_voice_response(
        cls,
        query: str,
        registry: CapabilityRegistry | None = None,
    ) -> ResponsePlan:
        from django.conf import settings

        registry = registry or CapabilityRegistry.from_settings()
        mode, required_capability = cls.classify(query)
        normalized = " ".join(query.split())
        if mode in {ResponseMode.NORMAL, ResponseMode.BRIEF} and _EXPLICIT_LONG_VOICE_FORMAT.search(normalized):
            mode = ResponseMode.DETAILED
        # Voice-only explicit elaboration; a plain "explain how X works"
        # remains NORMAL, and text-chat classification is unchanged.
        if mode == ResponseMode.NORMAL and re.search(
            r"\b(?:give|show|list|explain|describe|include)\b.*\b(?:steps|examples)\b",
            query, re.IGNORECASE,
        ):
            mode = ResponseMode.DETAILED
        budgets = {
            ResponseMode.BRIEF: int(
                getattr(settings, "VOICE_LLM_BRIEF_NUM_PREDICT", 48)
            ),
            ResponseMode.NORMAL: int(
                getattr(settings, "VOICE_LLM_NORMAL_NUM_PREDICT", 96)
            ),
            ResponseMode.DETAILED: int(
                getattr(settings, "VOICE_LLM_DETAILED_NUM_PREDICT", 192)
            ),
            ResponseMode.ACTION: int(
                getattr(settings, "VOICE_LLM_BRIEF_NUM_PREDICT", 48)
            ),
            ResponseMode.CLARIFICATION: int(
                getattr(settings, "VOICE_LLM_BRIEF_NUM_PREDICT", 48)
            ),
        }

        direct_response = None
        if mode == ResponseMode.CLARIFICATION:
            direct_response = "What would you like me to do?"
        elif (
            mode == ResponseMode.ACTION
            and required_capability is not None
            and not registry.is_available(required_capability)
        ):
            direct_response = registry.status(required_capability).unavailable_response

        instruction = f"{_VOICE_BASE_INSTRUCTION}\n\n{_MODE_INSTRUCTIONS[mode]}"
        return ResponsePlan(
            mode=mode,
            system_instruction=instruction,
            num_predict=budgets[mode],
            required_capability=required_capability,
            direct_response=direct_response,
            allow_implicit_rag=(required_capability == Capability.LOCAL_RAG
                                or bool(_LOCAL_KNOWLEDGE_INTENT.search(" ".join(query.split())))),
            is_voice=True,
            recent_history_limit=(min(4, max(0, int(getattr(settings, "VOICE_RECENT_MESSAGES", 2))))
                                  if _VOICE_REFERENCE.search(normalized) else 0),
            allow_long_term_memory=bool(_VOICE_RECALL.search(normalized)),
        )

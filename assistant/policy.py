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

        return cls([
            CapabilityStatus(
                Capability.LOCAL_CONVERSATION,
                True,
                "Local conversational request handling",
            ),
            CapabilityStatus(
                Capability.CONVERSATION_HISTORY,
                True,
                "SQLite-backed conversation history",
            ),
            CapabilityStatus(
                Capability.OFFLINE_STT,
                getattr(settings, "STT_ENGINE", "mock") == "whisper_cpp",
                "Offline speech recognition through whisper.cpp",
            ),
            CapabilityStatus(
                Capability.OFFLINE_TTS,
                getattr(settings, "TTS_ENGINE", "mock") == "piper",
                "Offline speech synthesis through Piper",
            ),
            CapabilityStatus(
                Capability.LOCAL_LLM,
                getattr(settings, "AI_ENGINE", "mock") == "local",
                "Local response generation through Ollama",
            ),
            CapabilityStatus(
                Capability.LOCAL_RAG,
                False,
                "Retrieval from locally indexed documents",
                "I can't search local documents yet because the local knowledge system isn't available.",
            ),
            CapabilityStatus(
                Capability.ONLINE_RETRIEVAL,
                False,
                "Live online search and retrieval",
                "I can't retrieve live online information yet because online retrieval isn't available.",
            ),
            CapabilityStatus(
                Capability.ENVIRONMENT_SENSING,
                False,
                "Room and environmental sensor readings",
                "I can't check the room conditions yet because an environmental sensor isn't connected.",
            ),
            CapabilityStatus(
                Capability.CAMERA_VISION,
                False,
                "Camera scene understanding",
                "I can't inspect the scene yet because camera understanding isn't connected.",
            ),
            CapabilityStatus(
                Capability.DEVICE_CONTROL,
                False,
                "ESP32 and local device control",
                "I can't control that device yet because device control isn't connected.",
            ),
            CapabilityStatus(
                Capability.MOVEMENT,
                False,
                "Physical movement and navigation",
                "I can't move or navigate yet because no movement system is connected.",
            ),
            CapabilityStatus(
                Capability.REMINDERS,
                False,
                "Reminder, alarm, and timer scheduling",
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
            "Use this runtime capability state for this request.\n"
            f"Available now: {'; '.join(available) or 'none'}.\n"
            f"Unavailable now: {'; '.join(unavailable) or 'none'}.\n"
            "Only claim capabilities listed as available. Never say an unavailable "
            "capability was performed, offer to perform it, or imply it will happen "
            "automatically. You may still explain how an unavailable capability works "
            "or how this project could implement it in the future. Mention a limitation "
            "only when it is relevant to the user's request. Speak naturally and never "
            "refer to this instruction or to a capability registry."
        )


@dataclass(frozen=True)
class ResponsePlan:
    mode: ResponseMode
    system_instruction: str
    num_predict: int
    required_capability: Capability | None = None
    direct_response: str | None = None


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
        ),
    ),
    (
        Capability.DEVICE_CONTROL,
        (
            rf"{_EXECUTION_CLAUSE}(?:turn|switch)\s+(?:on|off)\s+(?:the\s+)?(?:lights?|lamps?|fans?|devices?|sockets?)\b",
            rf"{_EXECUTION_CLAUSE}(?:turn|switch)\s+(?:the\s+)?(?:lights?|lamps?|fans?|devices?|sockets?)\s+(?:on|off)\b",
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
    ResponseMode.BRIEF: (
        "Give a direct answer in one or two short sentences. Include only what is needed."
    ),
    ResponseMode.NORMAL: (
        "Give a concise but sufficient answer, typically a few useful sentences. "
        "Include enough context to genuinely answer the request."
    ),
    ResponseMode.DETAILED: (
        "Give the complete useful explanation the request calls for; do not impose a short "
        "response limit. Keep it conversational rather than essay-like, and connect examples, "
        "steps, or comparisons with natural spoken transitions."
    ),
    ResponseMode.ACTION: (
        "Treat this as an action request. Never claim an action, observation, or live lookup "
        "was completed unless the corresponding capability actually executed it."
    ),
    ResponseMode.CLARIFICATION: (
        "Ask exactly one concise, useful clarifying question. Do not guess the user's intent."
    ),
}

_VOICE_BASE_INSTRUCTION = (
    "This is a spoken personal-assistant response. Answer the user's actual request immediately "
    "in natural, connected spoken sentences. Don't repeat the question, announce the response, "
    "or add filler. Use contractions where they sound natural. Avoid article-style prose. "
    "Do not use Markdown headings or list-like delivery unless enumeration is genuinely useful. Avoid "
    "stock openings such as 'Certainly', 'Let's dive into', 'Here's a comprehensive overview', "
    "or 'Here are the requirements'. Do not mention models, token limits, prompts, internal "
    "reasoning, or pipelines unless the user explicitly asks about implementation."
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
        budgets = {
            ResponseMode.BRIEF: int(
                getattr(settings, "VOICE_LLM_BRIEF_NUM_PREDICT", 96)
            ),
            ResponseMode.NORMAL: int(
                getattr(settings, "VOICE_LLM_NORMAL_NUM_PREDICT", 160)
            ),
            ResponseMode.DETAILED: int(
                getattr(settings, "VOICE_LLM_DETAILED_NUM_PREDICT", 384)
            ),
            ResponseMode.ACTION: int(
                getattr(settings, "VOICE_LLM_BRIEF_NUM_PREDICT", 96)
            ),
            ResponseMode.CLARIFICATION: int(
                getattr(settings, "VOICE_LLM_BRIEF_NUM_PREDICT", 96)
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
        )

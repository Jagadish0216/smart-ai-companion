# Architecture and capability boundaries

`config/urls.py` mounts dashboard routes and `/api/assistant/`, `/api/knowledge/`
and `/api/system/`. See each app's `urls.py` for exact supported endpoints.
Chat and streaming use `assistant/services.py`, request policy and deterministic
routing. `assistant/ai_engine` provides mock and local Ollama engines. SQLite
stores conversations and local document chunks. Conversation memory is scoped
lexical recall; it is not a general persistent user profile.

Local inference sends prompts to the configured Ollama endpoint. Offline use
requires weights already installed and every enabled provider to be local.
Optional SearXNG retrieval makes network requests. Offline inference does not
imply encrypted storage, universal access control or a guarantee that every
configuration keeps data on device.

`scripts/voice_loop.py` is an independent hardware entry point. It owns capture,
transcription, generation, synthesis/playback and display transitions. whisper.cpp
and Piper are external processes. Tests simulate much of their lifecycle; only
device tests establish microphone quality, thermal behavior and audible timing.

`assistant/display` sends serial state/text/audio-level messages to ESP32 display
firmware. This is distinct from `assistant/devices` and MQTT action/sensor
firmware. An available MQTT code path does not establish that the demonstration
included a temperature sensor or relay. Camera mounting provision in CAD does
not implement camera understanding. Movement and reminders remain future work.

Resource monitoring in `system` influences bounded execution policy. Provisioning
uses an independent NetworkManager workflow. See [engineering reference](ENGINEERING_REFERENCE.md)
for detailed budgets and protocols; its historical measurements retain their
original scope. [Deployment provenance](DEPLOYMENT.md) distinguishes the public
baseline from subsequent Pi changes.

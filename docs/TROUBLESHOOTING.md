# Troubleshooting

| Symptom | Check |
|---|---|
| Mock answer instead of model output | Set `AI_ENGINE=local`; confirm the private environment loaded and Ollama/model availability. |
| Different model than the final demonstration | Explicitly set `OLLAMA_MODEL=gemma4:e2b-it-q4_K_M`; historical defaults name Qwen. |
| No document grounding | RAG is disabled by default and for the reported final demonstration. If intentionally enabled, migrate, index a supported document and inspect its status. |
| No online facts | Online retrieval is optional and disabled by default; verify SearXNG only when enabled. |
| Browser voice unavailable | Verify browser secure-context/permission rules, FFmpeg and configured STT/TTS binaries; simulated tests do not certify LAN microphone access. |
| No physical speech or TFT feedback | Review the documented audio/serial ownership and stable device paths; do not guess GPIO or change a working service configuration. |
| Import error for pypdf | Activate the intended virtual environment and install requirements there. |
| Wake-model rename denied on Windows | Retry focused tests in an approved writable temp directory; the baseline hit a sandbox filesystem restriction. |
| Hotspot unavailable | Follow the dedicated first-boot setup guide; a unique setup passphrase and Linux NetworkManager are required. |

Use [SETUP.md](SETUP.md), [DEPLOYMENT.md](DEPLOYMENT.md) and the original
[engineering reference](ENGINEERING_REFERENCE.md) for scoped procedures. Inspect
logs privately; do not upload environment variables, conversation data or network
credentials with a bug report.

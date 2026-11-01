# Development setup

Use Python 3.10 or newer in a dedicated virtual environment. The cleanup was
verified with Python 3.10 on Windows. Full ARM64 package installation was not
repeated. Gunicorn is a Unix deployment server; use Django runserver on Windows.

```sh
git clone https://github.com/Jagadish0216/smart-ai-companion.git
cd smart-ai-companion
python -m venv venv
```

Activate with `venv\Scripts\Activate.ps1` in PowerShell or
`source venv/bin/activate` on Linux. Then:

```sh
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` (PowerShell: `Copy-Item .env.example .env`;
Linux: `cp .env.example .env`). Generate a unique key with
`python -c "import secrets; print(secrets.token_urlsafe(50))"` and place it in
the private file. Keep `AI_ENGINE=mock`, `STT_ENGINE=mock`, `TTS_ENGINE=mock`,
RAG, online retrieval, device control and the display disabled for development.
Do not copy a live Pi environment or database into Git.

```sh
python manage.py migrate
python manage.py createsuperuser
python manage.py check
python manage.py runserver 127.0.0.1:8000
```

Visit `http://127.0.0.1:8000/`. The default engine is a deterministic development
stub; a successful page or mock reply does not demonstrate model inference.
SQLite and uploaded documents remain local ignored runtime data.

## Optional local inference

Install Ollama separately using its official installer; acquire model weights
before going offline. The owner reports the completed Pi demonstration used:

```ini
AI_ENGINE=local
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_MODEL=gemma4:e2b-it-q4_K_M
RAG_ENABLED=false
ONLINE_RETRIEVAL_ENABLED=false
```

Confirm that the exact tag is available in your Ollama installation before
pulling or starting it. This audit did not download or execute that model.
Repository defaults and historical setup scripts still name Qwen; the override
above is deliberate. No model defaults or response policies were changed.

## Optional audio, wake word and documents

whisper.cpp and Piper executables and their model/voice files are external
prerequisites configured through `STT_*` and `TTS_*`. Browser recording conversion
uses FFmpeg. Inspect the preserved [engineering reference](ENGINEERING_REFERENCE.md)
before enabling audio. A browser microphone also needs a supported secure context;
LAN HTTP and mobile permissions are separate from mocked browser tests.

For Linux Python >=3.12 the requirements install ONNX prerequisites; install
`openwakeword==0.6.0` separately with `python -m pip install --no-deps openwakeword==0.6.0`.
Earlier Linux Python versions use the conditional normal installation and
TFLite wheel constraints. Wake models require explicit preparation through
`python manage.py prepare_wake_word_model`; models are not fetched at import.
Wake-word operation remains opt-in and needs acoustic validation on the device.

Local RAG uses SQLite lexical chunks, not embeddings. Enable `RAG_ENABLED=true`
only after migrations and document indexing. Staff upload supported text,
Markdown or text-layer PDF files at `/knowledge/`. Scanned PDFs need external
OCR and are not supported by this ingestion path. The final demonstration
reportedly disabled RAG; do not enable it to reproduce that configuration.

## Secure deployment boundary

For a shared installation set `DEBUG=False`, a unique `SECRET_KEY` and explicit
`ALLOWED_HOSTS`. Run `python manage.py check --deploy` and review every warning.
The defaults are development defaults, and `DEBUG=False` alone does not secure
the application. Public dashboard/chat/history and selected control/read APIs
are not a multi-user authorization boundary. Use a trusted isolated LAN or
authenticated reverse proxy; review network-control exposure before deployment.
Do not publish port 8000 or Ollama directly to the Internet.

For a new trusted-LAN installation, optional
`DJANGO_SETTINGS_MODULE=config.appliance_settings` enforces DEBUG=False,
a non-default 50+ character private secret and explicit hosts while preserving
HTTP/hotspot access. It does not add authentication, and no existing service
selects it automatically. Read [security boundaries](SECURITY.md) before opting
in or configuring HTTPS. Public Internet support needs separate review.

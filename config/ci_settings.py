"""Explicit non-deployment profile for hosted software tests; no model assets."""
from .settings import *  # noqa: F403

AI_ENGINE = 'mock'
STT_ENGINE = 'mock'
TTS_ENGINE = 'mock'
COMPANION_DISPLAY_ENABLED = False
DEVICE_CONTROL_ENABLED = False
RAG_ENABLED = False
ONLINE_RETRIEVAL_ENABLED = False
VOICE_WAKE_WORD_ENABLED = False
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}

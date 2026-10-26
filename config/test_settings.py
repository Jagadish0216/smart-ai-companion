"""Explicit regression profile. Never selected by application/voice services."""
from .settings import *  # noqa: F403
from assistant.test_support import VOICE_LOOP_TEST_SETTINGS, WAKE_WORD_TEST_SETTINGS

# Override Django settings only, not os.environ or any runtime configuration file.
globals().update(VOICE_LOOP_TEST_SETTINGS)
globals().update(WAKE_WORD_TEST_SETTINGS)
AI_ENGINE = 'mock'
STT_ENGINE = 'mock'
TTS_ENGINE = 'mock'
COMPANION_DISPLAY_ENABLED = False
RAG_ENABLED = False
ONLINE_RETRIEVAL_ENABLED = False
# Legacy model/resource fixtures deliberately exercise generation without a
# corpus. Integrated knowledge acceptance overrides this to the production True.
KNOWLEDGE_REQUIRE_EVIDENCE = False
# Preserve the established sync default; retrieval is disabled above and tests
# that opt in to persistence own their mocked/temporary storage.
ONLINE_KNOWLEDGE_AUTO_PERSIST = True
DEVICE_CONTROL_ENABLED = False
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:',
                         'TEST': {'NAME': ':memory:'}}}

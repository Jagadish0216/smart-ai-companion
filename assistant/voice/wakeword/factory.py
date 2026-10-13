"""Select a provider without importing or warming the third-party engine."""

from .base import DisabledWakeWordProvider, WakeWordConfig


def get_wake_word_provider(config=None):
    config = config if config is not None else WakeWordConfig.from_settings()
    config.validate()
    if not config.enabled:
        return DisabledWakeWordProvider()
    from .openwakeword_provider import OpenWakeWordProvider
    return OpenWakeWordProvider(config)

"""Configuration checks do not open storage or contact the online provider."""

from django.core.checks import Error, register

from .online_sync import OnlineSyncConfig


@register()
def check_online_knowledge_config(app_configs, **kwargs):
    try:
        OnlineSyncConfig.from_settings()
    except ValueError as exc:
        return [Error(str(exc), id='knowledge_base.E001')]
    return []

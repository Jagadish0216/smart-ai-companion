from django.conf import settings
from django.core.cache import cache

from system.control_plane.network import get_internet_status


HEADER_INTERNET_CACHE_KEY = 'dashboard.header.internet-state.v1'
HEADER_INTERNET_CACHE_SECONDS = 10
INTERNET_PRESENTATION = {
    'FULL': ('ONLINE', 'online'),
    'LIMITED': ('LIMITED', 'limited'),
    'NONE': ('OFFLINE', 'offline'),
    'UNKNOWN': ('UNKNOWN', 'unknown'),
}


def _cached_internet_state():
    internet_state = cache.get(HEADER_INTERNET_CACHE_KEY)
    if internet_state in INTERNET_PRESENTATION:
        return internet_state

    try:
        internet_state = str(get_internet_status().get('state', 'UNKNOWN')).upper()
    except Exception:
        internet_state = 'UNKNOWN'
    if internet_state not in INTERNET_PRESENTATION:
        internet_state = 'UNKNOWN'
    cache.set(
        HEADER_INTERNET_CACHE_KEY,
        internet_state,
        HEADER_INTERNET_CACHE_SECONDS,
    )
    return internet_state


def system_status(request):
    ai_engine_name = str(getattr(settings, 'AI_ENGINE', 'mock')).strip().upper()
    ai_engine_name = ai_engine_name or 'MOCK'
    ai_model = (
        str(getattr(settings, 'OLLAMA_MODEL', 'Unknown'))
        if ai_engine_name == 'LOCAL'
        else 'Development'
    )
    internet_state = _cached_internet_state()
    internet_label, internet_class = INTERNET_PRESENTATION[internet_state]

    return {
        'global_ai_engine': ai_engine_name,
        'global_ai_model': ai_model,
        'global_internet_state': internet_state,
        'global_internet_label': internet_label,
        'global_internet_class': internet_class,
    }

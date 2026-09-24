from django.conf import settings
from assistant.ai_engine import get_engine

def system_status(request):
    try:
        engine = get_engine()
        ai_engine_name = engine.engine_name.upper()
    except Exception:
        ai_engine_name = getattr(settings, 'AI_ENGINE', 'MOCK').upper()

    ai_model = getattr(settings, 'OLLAMA_MODEL', 'Unknown') if ai_engine_name == 'LOCAL' else 'Mocked'
    
    return {
        'global_ai_engine': ai_engine_name,
        'global_ai_model': ai_model,
        'global_ai_mode': 'Offline'
    }

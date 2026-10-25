"""Deterministic discovery hints; never add people, projects, or factual answers."""
import re


INDUSTRIES = {'tollywood': 'Telugu', 'kollywood': 'Tamil',
              'mollywood': 'Malayalam', 'bollywood': 'Hindi'}


def normalized_words(text):
    text = re.sub(r'\bartificial intelligence\b', 'ai', text, flags=re.I)
    text = re.sub(r"['’]s\b", '', text)
    aliases = {**{k: v.lower() for k, v in INDUSTRIES.items()},
               'tech': 'technology', 'models': 'model', 'developments': 'development',
               'technologies': 'technology', 'announcements': 'announcement'}
    aliases.update({'scientists': 'science', 'discoveries': 'discovery', 'discovered': 'discovery'})
    return {aliases.get(word, word) for word in re.findall(r'[a-z0-9]+', text.lower())}


def search_queries(query):
    """At most two distinct queries, retaining named entities and requested dates."""
    from .current_sources import current_topic
    primary = re.sub(r"^(?:please\s+)?(?:(?:can|could|would) you\s+)?(?:please\s+)?(?:tell me(?: about)?|give me|show me|list)\s+", '', query.strip(), flags=re.I)
    primary = re.sub(r"^(?:what(?:'s| is| are)|which are)\s+(?:the\s+)?", '', primary, flags=re.I)
    for industry, language in INDUSTRIES.items():
        primary = re.sub(r'\b'+industry+r'\b', language+' cinema', primary, flags=re.I)
    primary = primary.strip(' ?.!') or query.strip()
    topic = current_topic(query)
    if topic == 'entertainment':
        suffix = 'release schedule' if re.search(r'\breleas', query, re.I) else 'production announcement'
        alternative = primary+' '+suffix
    elif topic == 'officeholder':
        from .current_sources import known_office_profile
        url = known_office_profile(query)
        alternative = primary+' site:'+url.split('://', 1)[1].rstrip('/') if url else ''
    elif re.search(r'\brelease|\bversion', query, re.I):
        alternative = primary+' release notes'
    else:
        alternative = primary+' announcement report'
    # Preserve the existing bounded query policy even after adding search hints.
    from .searxng import _bounded_query
    return tuple(dict.fromkeys(_bounded_query(value) for value in (primary, alternative) if value))


def search_filters(query):
    if re.search(r'\b(?:this week|past week)\b', query, re.I):
        return {'time_range': 'week'}
    if re.search(r'\b(?:today|breaking|headlines?)\b', query, re.I):
        return {'categories': 'news', 'time_range': 'day'}
    if re.search(r'\b(?:news|current affairs)\b', query, re.I):
        return {'categories': 'news', 'time_range': 'week'}
    return {}

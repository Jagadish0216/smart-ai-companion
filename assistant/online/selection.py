"""Bounded candidate ranking, independent of search engine position."""
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


def canonical_url(url):
    p = urlsplit(url)
    query = urlencode([(k,v) for k,v in parse_qsl(p.query) if not k.lower().startswith('utm_') and k.lower() not in {'fbclid','gclid'}])
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip('/') or '/', query, ''))


def is_article(result):
    path = urlsplit(result.url).path.strip('/').lower()
    return bool(path and path not in {'news','blog','search','category','tag','tags','categories'} and path.split('/')[0] not in {'search','category','tag','tags','categories'})


def rank_candidates(results, query):
    from .searxng import query_terms
    from .grounding import select_current_evidence
    terms = query_terms(query)
    seen = set()
    ranked = []
    for result in results:
        from .enrichment import cached_accessibility
        if cached_accessibility(result.url) == 'unavailable':
            continue
        identity = canonical_url(result.url)
        if identity in seen:
            continue
        seen.add(identity)
        words = set(re.findall(r'[a-z0-9]+',(result.title+' '+result.content).lower()))
        overlap = len(terms & words)
        informative = len(result.content.split()) >= 4 and not re.search(r'\b(?:shop now|buy now|sponsored|discount codes?|special offers?|read more|click here)\b',result.content,re.I)
        host = urlsplit(result.url).hostname or ''
        primary = {'raspberry','pi'} <= terms and (host == 'raspberrypi.com' or host.endswith('.raspberrypi.com'))
        from .current_sources import official_office_page
        official = official_office_page(result.url, query)
        fresh = bool(select_current_evidence([result],query,datetime.now(timezone.utc)))
        date_quality = 2 if fresh else 1 if result.published_at is None and not result.date_conflict else 0
        score = (official, overlap >= min(2,len(terms)) if terms else True, informative, is_article(result), date_quality, overlap, primary)
        ranked.append((score,result))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [result for _,result in ranked[:20]]


def diverse_selection(results, limit):
    if limit <= 0:
        return []
    selected, deferred, hosts = [], [], {}
    for result in results:
        host = (urlsplit(result.url).hostname or '').removeprefix('www.')
        if hosts.get(host,0) >= 2:
            deferred.append(result)
            continue
        selected.append(result); hosts[host]=hosts.get(host,0)+1
        if len(selected) == limit:
            return selected
    return (selected+deferred)[:limit]

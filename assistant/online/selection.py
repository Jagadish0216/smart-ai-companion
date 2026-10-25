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


def reporting_query(query):
    from .evidence_policy import evidence_category
    return evidence_category(query) in {'entertainment', 'current'}


def candidate_kind(result, query):
    """Discovery hints only, never a publisher trust/allowlist decision."""
    if not reporting_query(query):
        return 'neutral', 1
    parsed = urlsplit(result.url)
    host, path = (parsed.hostname or '').lower(), parsed.path.lower()
    def domain(*names):
        return any(host == name or host.endswith('.'+name) for name in names)
    if domain('wikipedia.org', 'wikidata.org'):
        return 'reference', 0
    if domain('quora.com', 'answers.com') or re.search(r'/(?:questions?|answers?|forums?)(?:/|$)', path):
        return 'qa_or_forum', 0
    if domain('facebook.com', 'instagram.com', 'twitter.com', 'x.com', 'tiktok.com'):
        return 'social_feed', 0
    if re.search(r'\b(?:biography|filmography|biographies|filmographies)\b', result.title, re.I) or re.search(r'/(?:biography|filmography)(?:/|$)', path):
        return 'biography', 0
    if not is_article(result) or re.search(r'/(?:movies|films|releases|listings|archive|archives)/?$', path):
        return 'generic_listing', 0
    if result.published_at and not result.date_conflict:
        age = (datetime.now(timezone.utc)-result.published_at).total_seconds()
        age_limit = 2*86400 if re.search(r'\b(?:news|recent|today|events?)\b', query, re.I) else 30*86400
        if re.search(r'\b(?:weather|forecast)\b', query, re.I):
            age_limit = 3*3600
        if re.search(r'\b(?:prices?|scores?|status|live|traffic|stocks?)\b', query, re.I):
            age_limit = 15*60
        if 0 <= age <= age_limit:
            return 'dated_report', 3
        return 'stale_or_future_report', 1
    return 'article_candidate', 2


def enrichment_order(results, query):
    """Prioritize reporting before host diversity and defer low-value alternatives."""
    from .searxng import query_terms
    from .intent import normalized_words
    terms = query_terms(query)
    values = []
    occurrences = {}
    for index, result in enumerate(results):
        kind, priority = candidate_kind(result, query)
        host = (urlsplit(result.url).hostname or '').removeprefix('www.')
        occurrence = occurrences.get(host, 0)
        occurrences[host] = occurrence+1
        relevant = len(terms & normalized_words(result.title+' '+result.content)) >= min(2, len(terms))
        values.append((index, kind, priority, relevant, occurrence))
    promising = reporting_query(query) and any(priority >= 2 and relevant for _, _, priority, relevant, _ in values)
    values.sort(key=lambda value: (not value[3], -value[2], value[4], value[0]))
    return [(index, kind, promising and priority == 0) for index, kind, priority, _, _ in values]


def rank_candidates(results, query):
    from .searxng import query_terms
    from .intent import normalized_words
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
        words = normalized_words(result.title+' '+result.content)
        overlap = len(terms & words)
        informative = len(result.content.split()) >= 4 and not re.search(r'\b(?:shop now|buy now|sponsored|discount codes?|special offers?|read more|click here)\b',result.content,re.I)
        host = urlsplit(result.url).hostname or ''
        primary = {'raspberry','pi'} <= terms and (host == 'raspberrypi.com' or host.endswith('.raspberrypi.com'))
        from .current_sources import official_office_page
        official = official_office_page(result.url, query)
        fresh = bool(select_current_evidence([result],query,datetime.now(timezone.utc)))
        date_quality = 2 if fresh else 1 if result.published_at is None and not result.date_conflict else 0
        _, priority = candidate_kind(result, query)
        score = (official, overlap >= min(2,len(terms)) if terms else True, informative, priority, is_article(result), date_quality, overlap, primary)
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

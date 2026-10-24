"""Intent-specific evidence rules, not relaxed news thresholds."""
import re
from urllib.parse import urlsplit
from .dates import parse_publication_date


def evidence_category(query):
    if re.search(r'\b(news|headlines?|breaking|weather|prices?|scores?|status|traffic|stocks?|live)\b',query,re.I):
        return 'current'
    if re.search(r'\b(versions?|releases?)\b',query,re.I):
        return 'release'
    from assistant.routing import QueryRouter
    topic=re.sub(r'\b(?:search|browse|check)\s+(?:the\s+)?(?:web|internet|online)(?:\s+for)?|\blook\s+(?:this\s+)?up\s+online\b','',query,flags=re.I)
    return 'current' if QueryRouter.requires_online(topic, respect_offline=False) else 'technical'


def official_catalog(url):
    p=urlsplit(url)
    return p.scheme=='https' and p.hostname in {'raspberrypi.com','www.raspberrypi.com'} and p.path.rstrip('/')=='/software/operating-systems'


def release_statement(text, url):
    """Extract explicitly labeled OS catalog facts, never dates from URL/title.

    Scope deliberately limited to the official Raspberry Pi download catalog.
    A release date describes a software release, not page publication.
    """
    if not official_catalog(url) or 'Raspberry Pi OS' not in text:
        return None, ''
    first=re.search(r'Release date',text,re.I)
    if not first:
        return None,''
    text=text[first.start():]
    # Ignore navigation links before the first table and separate Legacy and
    # Desktop catalogs after the primary OS tables.
    text=re.split(r'Raspberry Pi OS\s*\(?Legacy|Raspberry Pi Desktop',text,maxsplit=1,flags=re.I)[0]
    dates=re.findall(r'Release date\s*[:|]?\s*(\d{4}-\d{2}-\d{2}|\d{1,2} [A-Za-z]+ \d{4})',text,re.I)
    parsed=[parse_publication_date(d) for d in dates]
    parsed=[d for d in parsed if d]
    versions=re.findall(r'Debian version\s*[:|]?\s*(\d{1,2}(?:\.\d+)?(?:\s*\([A-Za-z]+\))?)',text,re.I)
    # Multiple distinct release dates/versions might describe different images.
    # Do not choose one or synthesize a release/version pairing.
    if not parsed or len(set(parsed))!=1 or len(set(versions))!=1:
        return None,''
    return parsed[0],f'Official Raspberry Pi OS download catalog: Debian version: {versions[0]}; Release date: {parsed[0].date().isoformat()}. This is a release date, not a page publication date.'

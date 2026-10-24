"""Bounded article metadata discovery, without crawlers, scripts or proxies."""
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import replace
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin

from .dates import consistent_date, parse_publication_date

_CACHE = OrderedDict()
_LOCK = threading.Lock()
_SLOT = threading.Lock()
_DNS_SLOT = threading.Lock()
_HOST_BACKOFF = OrderedDict()
MAX_BYTES = 131072


class PageUnavailable(ValueError):
    def __init__(self, status):
        self.status = status
        super().__init__('Article unavailable')


def cached_accessibility(url):
    from .selection import canonical_url
    with _LOCK:
        cached = _CACHE.get(canonical_url(url))
    return cached[1][3] if cached and cached[0] > time.monotonic() and len(cached[1]) > 3 else 'unchecked'


def _timeout(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Article budget exhausted')
    return min(1.0, remaining)


class ArticleMetadata(HTMLParser):
    def __init__(self, page_url=None):
        super().__init__(convert_charrefs=True)
        self.published = []
        self.modified = []
        self.json_parts = None
        self.page_url = page_url
        self.redirect = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            if attrs.get('http-equiv','').lower() == 'refresh':
                self.redirect = True
            key = (attrs.get('property') or attrs.get('name') or attrs.get('itemprop') or '').lower()
            value = attrs.get('content', '')
            if key in {'article:published_time', 'og:published_time', 'datepublished', 'pubdate', 'publishdate', 'publication_date', 'citation_publication_date', 'citation_date', 'dc.date.issued', 'dcterms.issued'}:
                self.published.append(value)
            elif key in {'article:modified_time', 'datemodified', 'last-modified'}:
                self.modified.append(value)
        if tag == 'time' and attrs.get('itemprop', '').lower() == 'datepublished':
            self.published.append(attrs.get('datetime', ''))
        if tag == 'script' and attrs.get('type', '').lower() == 'application/ld+json':
            self.json_parts = []

    def handle_data(self, data):
        if self.json_parts is not None:
            self.json_parts.append(data)

    def handle_endtag(self, tag):
        if tag != 'script' or self.json_parts is None:
            return
        try:
            data = json.loads(''.join(self.json_parts))
            stack = [data]
            for _ in range(100):
                if not stack:
                    break
                node = stack.pop()
                if isinstance(node, list):
                    stack.extend(node[:100])
                elif isinstance(node, dict):
                    types = node.get('@type', [])
                    types = [types] if isinstance(types, str) else types
                    article_url = node.get('url') or node.get('mainEntityOfPage')
                    if isinstance(article_url, dict):
                        article_url = article_url.get('@id')
                    belongs = True
                    if self.page_url and isinstance(article_url, str):
                        candidate = urlsplit(urljoin(self.page_url, article_url))
                        page = urlsplit(self.page_url)
                        belongs = (candidate.scheme, candidate.netloc, candidate.path.rstrip('/')) == (page.scheme, page.netloc, page.path.rstrip('/'))
                    if belongs and isinstance(types, list) and any(t in {'Article', 'NewsArticle', 'BlogPosting', 'TechArticle', 'Report'} for t in types if isinstance(t, str)):
                        self.published.append(node.get('datePublished'))
                        self.modified.append(node.get('dateModified'))
                    for key in ('@graph', 'mainEntity'):
                        if key in node:
                            stack.append(node[key])
        except (ValueError, TypeError, RecursionError):
            pass
        finally:
            self.json_parts = None


def public_target(url, deadline=None):
    """Resolve once, reject *any* unsafe address, and pin the connection to it."""
    from .searxng import _safe_public_url
    if not _safe_public_url(url):
        raise ValueError('Unsafe article URL')
    parsed = urlsplit(url)
    if parsed.port not in (None, 80, 443):
        raise ValueError('Nonstandard article port')
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    if not _DNS_SLOT.acquire(blocking=False):
        raise TimeoutError('Resolver busy')
    complete = threading.Event()
    outcome = []
    def resolve():
        try:
            outcome.append(socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM))
        except OSError:
            outcome.append([])
        finally:
            _DNS_SLOT.release()
            complete.set()
    threading.Thread(target=resolve, daemon=True).start()
    if not complete.wait(max(0, min(1.0, (deadline or time.monotonic() + 1) - time.monotonic()))):
        raise TimeoutError('Resolver timeout')
    addresses = outcome[0]
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Unsafe article address')
    return parsed, port, addresses[0][4][0]


def fetch_metadata(url, deadline):
    for hop in range(3):
        if time.monotonic() >= deadline:
            raise TimeoutError()
        parsed, port, address = public_target(url, deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        connection = http.client.HTTPConnection(parsed.hostname, port, timeout=_timeout(deadline))
        guard = None
        try:
            # No second DNS lookup: TLS still verifies the original hostname.
            connection.sock = socket.create_connection((address, port), timeout=_timeout(deadline))
            if parsed.scheme == 'https':
                connection.sock.settimeout(_timeout(deadline))
                connection.sock = ssl.create_default_context().wrap_socket(connection.sock, server_hostname=parsed.hostname)
            transport = connection.sock
            def abort(transport=transport):
                try:
                    transport.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            # read1 checks cannot bound a slow-drip HTTP header; interrupt the
            # pinned socket at the absolute deadline as well.
            guard = threading.Timer(max(0, deadline-time.monotonic()), abort)
            guard.daemon = True
            guard.start()
            path = parsed.path or '/'
            if parsed.query:
                path += '?' + parsed.query
            transport.settimeout(_timeout(deadline))
            connection.request('GET', path, headers={'Accept': 'text/html,application/xhtml+xml', 'Accept-Encoding': 'identity', 'User-Agent': 'smart-ai-companion/1.0'})
            transport.settimeout(_timeout(deadline))
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                target = urljoin(url, response.getheader('Location', ''))
                if hop == 2 or (parsed.scheme == 'https' and urlsplit(target).scheme != 'https'):
                    raise ValueError('Unsafe article redirect')
                url = target
                continue  # Validate and pin every redirect before any request.
            if response.status != 200:
                raise PageUnavailable(response.status)
            if response.getheader('Content-Type', '').split(';')[0].strip().lower() not in {'text/html', 'application/xhtml+xml'}:
                raise ValueError('Not an HTML article')
            if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                raise ValueError('Compressed article refused')
            body = bytearray()
            while len(body) <= MAX_BYTES:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                transport.settimeout(min(1.0, remaining))
                chunk = response.read1(min(8192, MAX_BYTES + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise ValueError('Article too large')
            if re.search(rb'<title[^>]*>\s*(?:404|403|429|not found|page not found|access denied|just a moment|service unavailable|temporarily unavailable|too many requests|forbidden|captcha|error\b)', body, re.I):
                raise ValueError('HTML error or challenge page')
            parser = ArticleMetadata(url)
            parser.feed(body.decode('utf-8', errors='replace'))
            parser.close()
            if parser.redirect:
                raise ValueError('HTML redirect refused')
            valid = [parse_publication_date(v) for v in parser.published]
            valid = [v for v in valid if v]
            return consistent_date(parser.published), consistent_date(parser.modified), len({v.date() for v in valid}) > 1
        finally:
            if guard is not None:
                guard.cancel()
            connection.close()
    raise ValueError('Too many article redirects')


def enrich_results(results, query, deadline, target=4):
    """Four candidate attempts at most; unchanged shared 2s sub-budget.

    A nonblocking OS lock also caps cooperating Pi service workers to one fetch
    pipeline. Contention abstains instead of queuing. No changes to resource policy.
    """
    from .searxng import query_terms
    deadline = min(deadline, time.monotonic() + 2.0)
    terms = query_terms(query)
    enriched = list(results)
    attempts = 0
    occurrences, order = {}, []
    for index, result in enumerate(results):
        host = (urlsplit(result.url).hostname or '').removeprefix('www.')
        order.append((occurrences.get(host,0), index))
        occurrences[host] = occurrences.get(host,0)+1
    # First promising article from each hostname before its second article.
    for _, index in sorted(order):
        result = results[index]
        from .selection import is_article, canonical_url
        from .grounding import select_current_evidence
        from datetime import datetime, timezone
        if len(select_current_evidence(enriched, query, datetime.now(timezone.utc))) >= target:
            break
        cache_key = canonical_url(result.url)
        if not is_article(result):
            continue
        host = urlsplit(result.url).hostname
        with _LOCK:
            blocked = _HOST_BACKOFF.get(host, 0) > time.monotonic()
        if blocked:
            continue
        if urlsplit(result.url).path.strip('/').lower() in {'', 'news', 'blog', 'search', 'category', 'tag'}:
            continue  # Landing-page metadata cannot date linked articles.
        words = set(re.findall(r'[a-z0-9]+', (result.title + ' ' + result.content).lower()))
        if result.published_at or result.date_conflict or not terms or len(terms & words) < min(2, len(terms)) or re.search(r'\b(?:shop now|buy now|discount codes?|sponsored|special offers?)\b', result.content, re.I):
            continue
        with _LOCK:
            cached = _CACHE.get(cache_key)
        if cached and cached[0] > time.monotonic():
            metadata = cached[1]
        else:
            if attempts >= 4 or time.monotonic() >= deadline or not _SLOT.acquire(blocking=False):
                continue
            guard = None
            try:
                guard = os.open(os.path.join(tempfile.gettempdir(), 'smart-companion-metadata.lock'), os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                if os.name == 'posix':
                    import fcntl
                    fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    import msvcrt
                    os.write(guard, b'0')
                    os.lseek(guard, 0, 0)
                    msvcrt.locking(guard, msvcrt.LK_NBLCK, 1)
                attempts += 1
                metadata = (*fetch_metadata(result.url, min(deadline, time.monotonic()+.65)), 'available')
            except (OSError, ValueError, http.client.HTTPException, RecursionError) as failure:
                if isinstance(failure, PageUnavailable) and failure.status in (403,429):
                    with _LOCK:
                        _HOST_BACKOFF[host] = time.monotonic()+60
                        while len(_HOST_BACKOFF) > 32:
                            _HOST_BACKOFF.popitem(last=False)
                metadata = (None, None, False, 'unavailable')
            finally:
                if guard is not None:
                    os.close(guard)
                _SLOT.release()
            with _LOCK:
                _CACHE[cache_key] = (time.monotonic() + (300 if metadata[0] else 15), metadata)
                _CACHE.move_to_end(cache_key)
                while len(_CACHE) > 64:
                    _CACHE.popitem(last=False)
        published, modified, conflict = metadata[:3]
        accessibility = metadata[3] if len(metadata) > 3 else 'available'
        enriched[index] = replace(result, published_at=published, modified_at=modified or result.modified_at, publication_source='article_metadata' if published else '', date_conflict=conflict, accessibility=accessibility)
    return enriched

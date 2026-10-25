"""Bounded article metadata discovery, without crawlers, scripts or proxies."""
import http.client
import ipaddress
import json
import logging
import os
import re
import socket
import ssl
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import replace
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin

from .dates import consistent_date, parse_publication_date

_CACHE = OrderedDict()
_LOCK = threading.Lock()
_SLOT = threading.Lock()
_DNS_SLOT = threading.Lock()
_HOST_BACKOFF = OrderedDict()
MAX_BYTES = 262144
logger = logging.getLogger(__name__)


def fetch_failure_reason(failure):
    """Fixed labels only: exception text can contain private network details."""
    from .partial_html import PartialEvidenceError
    if isinstance(failure, PartialEvidenceError):
        return failure.reason
    if getattr(failure, 'deadline_exhausted', False):
        return 'timeout'
    if isinstance(failure, PageUnavailable):
        return 'http_'+str(failure.status)
    if isinstance(failure, TimeoutError):
        return 'timeout'
    if isinstance(failure, ssl.SSLError):
        return 'tls_error'
    messages = {'Unsafe article URL': 'unsafe_url', 'Unsafe article address': 'unsafe_address',
                'Nonstandard article port': 'unsafe_port', 'Unsafe article redirect': 'unsafe_redirect',
                'Not an HTML article': 'unsupported_type', 'Compressed article refused': 'unsupported_compression',
                'HTML error or challenge page': 'challenge_page', 'HTML redirect refused': 'html_redirect'}
    messages.update({'Incomplete article response': 'incomplete_response', 'Invalid article length': 'invalid_length'})
    if isinstance(failure, ValueError):
        return messages.get(str(failure), 'invalid_page')
    return 'network_or_parse_error'


class PageUnavailable(ValueError):
    def __init__(self, status):
        self.status = status
        super().__init__('Article unavailable')


def _check_partial(result, page, published, conflict, query, topic, needs_catalog):
    if not page.get('partial_response'):
        return
    from .partial_html import PartialEvidenceError
    from .grounding import has_conflicting_claims
    if (topic == 'officeholder' or needs_catalog or not published or conflict
            or page.get('partial_scope') != 'closed_article'):
        raise PartialEvidenceError('partial_scope_unsupported')
    if has_conflicting_claims([replace(result, content=page.get('text', ''))], query):
        raise PartialEvidenceError('partial_conflicting_claims')


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
        self.events = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            if attrs.get('http-equiv','').lower() == 'refresh':
                self.redirect = True
            key = (attrs.get('property') or attrs.get('name') or attrs.get('itemprop') or '').lower()
            value = attrs.get('content', '')
            if key in {'article:published_time', 'og:published_time', 'datepublished', 'pubdate', 'publishdate', 'publish-date', 'publication_date', 'citation_publication_date', 'citation_date', 'dc.date.issued', 'dcterms.issued', 'parsely-pub-date', 'sailthru.date'}:
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
                    if belongs and isinstance(types, list) and any(t in {'Event', 'ScreeningEvent', 'Movie'} for t in types if isinstance(t, str)):
                        self.events.append(node.get('startDate'))
                    for key in ('@graph', 'mainEntity'):
                        if key in node:
                            stack.append(node[key])
        except (ValueError, TypeError, RecursionError):
            pass
        finally:
            self.json_parts = None


class PageText(HTMLParser):
    """Visible body blocks only; never execute scripts or use navigation as evidence."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], []

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'template', 'nav', 'header', 'footer', 'head', 'title', 'iframe'}:
            self.hidden.append(tag)
        if tag in {'p', 'div', 'h1', 'h2', 'h3', 'li', 'tr', 'br'} and not self.hidden:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.hidden and tag == self.hidden[-1]:
            self.hidden.pop()
        if tag in {'p', 'div', 'h1', 'h2', 'h3', 'li', 'tr'} and not self.hidden:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


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
    started = time.monotonic()
    try:
        value = _fetch_metadata(url, deadline)
    except (OSError, ValueError, http.client.HTTPException, RecursionError) as failure:
        logger.info('Article fetch completed', extra={'article_fetch': {
            'outcome': 'rejected', 'reason': fetch_failure_reason(failure),
            'http_status': failure.status if isinstance(failure, PageUnavailable) else None,
            'bytes_read': getattr(failure, 'bytes_read', 0),
            'partial_response': getattr(failure, 'partial_response', False),
            'redirect_hops': getattr(failure, 'redirect_hops', 0),
            'elapsed_ms': round((time.monotonic()-started)*1000),
        }})
        raise
    page = value[5]
    logger.info('Article fetch completed', extra={'article_fetch': {
        'outcome': 'partial' if page.get('partial_response') else 'complete',
        'reason': 'closed_article' if page.get('partial_response') else 'complete_response',
        'bytes_read': page.get('bytes_read', 0), 'redirect_hops': page.get('redirect_hops', 0),
        'elapsed_ms': round((time.monotonic()-started)*1000),
    }})
    return value


def _fetch_metadata(url, deadline):
    for hop in range(3):
        if time.monotonic() >= deadline:
            raise TimeoutError()
        parsed, port, address = public_target(url, deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        connection = http.client.HTTPConnection(parsed.hostname, port, timeout=_timeout(deadline))
        guard = None
        body = None
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
            declared = response.getheader('Content-Length', '')
            if declared and (not isinstance(declared, str) or not declared.isdigit() or len(declared) > 20):
                raise ValueError('Invalid article length')
            declared = int(declared) if declared else None
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
            if len(body) <= MAX_BYTES and declared is not None and declared != len(body):
                raise ValueError('Incomplete article response')
            if re.search(rb'<title[^>]*>\s*(?:404|403|429|not found|page not found|access denied|just a moment|service unavailable|temporarily unavailable|too many requests|forbidden|captcha|error\b)', body, re.I):
                raise ValueError('HTML error or challenge page')
            if len(body) > MAX_BYTES:
                from .partial_html import extract_partial_article
                partial = extract_partial_article(body[:MAX_BYTES], url, deadline)
                partial[5].update(bytes_read=len(body), redirect_hops=hop)
                return partial
            if time.monotonic() >= deadline:
                raise TimeoutError()
            parser = ArticleMetadata(url)
            parser.feed(body.decode('utf-8', errors='replace'))
            parser.close()
            if parser.redirect:
                raise ValueError('HTML redirect refused')
            valid = [parse_publication_date(v) for v in parser.published]
            valid = [v for v in valid if v]
            from .evidence_policy import release_statement
            from .searxng import _plain_text
            release_date, release_text = release_statement(_plain_text(body.decode('utf-8',errors='replace')), url)
            visible = PageText()
            visible.feed(body.decode('utf-8', errors='replace'))
            text = '\n'.join(clean for line in ''.join(visible.parts).splitlines() if (clean := ' '.join(line.split())))
            # Only explicitly labelled publication dates in the visible article
            # header; never URL/title years, event dates, or "updated" labels.
            for value in re.findall(r'(?im)^Published(?: on)?\s*:?\s*((?:20\d{2}-\d{2}-\d{2}|\d{1,2} [A-Za-z]+ 20\d{2}|[A-Za-z]+ \d{1,2},? 20\d{2}))\b', text[:1200]):
                parser.published.append(value)
            valid = [stamp for value in parser.published if (stamp := parse_publication_date(value))]
            basic = (consistent_date(parser.published), consistent_date(parser.modified), len({v.date() for v in valid}) > 1)
            if time.monotonic() >= deadline:
                raise TimeoutError()
            return basic + (release_date, release_text, {'text': text[:12000], 'url': url,
                            'partial_response': False, 'bytes_read': len(body), 'redirect_hops': hop,
                            'event_at': consistent_date(parser.events)})
        except (OSError, ValueError, http.client.HTTPException, RecursionError) as failure:
            failure.bytes_read = len(body) if body is not None else 0
            failure.partial_response = body is not None and len(body) > MAX_BYTES
            failure.redirect_hops = hop
            failure.deadline_exhausted = time.monotonic() >= deadline
            raise
        finally:
            if guard is not None:
                guard.cancel()
            connection.close()
    raise ValueError('Too many article redirects')


def enrich_results(results, query, deadline, target=4, attempt_budget=None, diagnostics=None):
    """Four candidate attempts at most; unchanged shared 2s sub-budget.

    A nonblocking OS lock also caps cooperating Pi service workers to one fetch
    pipeline. Contention abstains instead of queuing. No changes to resource policy.
    """
    from .searxng import query_terms
    from .intent import normalized_words
    from .facts import factual_question
    from .current_sources import current_topic, official_office_page, page_evidence, classify_claims
    topic = current_topic(query)
    needs_fact_body = bool(factual_question(query))
    deadline = min(deadline, time.monotonic() + 2.0)
    terms = query_terms(query)
    enriched = list(results)
    attempts = 0
    if attempt_budget is None:
        attempt_budget = [4]
    from .selection import enrichment_order
    if diagnostics is None:
        diagnostics = {}
    def count(group, label):
        counters = diagnostics.setdefault(group, {})
        counters[label] = counters.get(label, 0)+1
    order = enrichment_order(results, query)
    for _, kind, deferred in order:
        count('candidate_classes', kind)
        if deferred:
            count('candidate_decisions', 'deferred_low_value')
    # Preserve host diversity within each reporting-priority tier.
    for index, kind, deferred in order:
        if deferred:
            continue
        result = results[index]
        from .selection import is_article, canonical_url
        from .grounding import select_current_evidence
        admitted = select_current_evidence(enriched, query, datetime.now(timezone.utc))
        if needs_fact_body:
            admitted = [r for r in admitted if r.accessibility == 'available'
                        and r.evidence_kind in {'fetched_reference', 'fetched_report'}
                        and r.publication_source == 'article_metadata']
        if len(admitted) >= target:
            count('candidate_decisions', 'evidence_target_met')
            break
        cache_key = canonical_url(result.url)
        official = official_office_page(result.url, query)
        if topic == 'officeholder' and not official:
            count('candidate_decisions', 'not_official_profile')
            continue  # Dated reporting cannot verify the present office roster.
        if not is_article(result) and not official:
            count('candidate_decisions', 'not_article')
            continue
        host = urlsplit(result.url).hostname
        with _LOCK:
            blocked = _HOST_BACKOFF.get(host, 0) > time.monotonic()
        if urlsplit(result.url).path.strip('/').lower() in {'', 'news', 'blog', 'search', 'category', 'tag'}:
            count('candidate_decisions', 'landing_page')
            continue  # Landing-page metadata cannot date linked articles.
        words = normalized_words(result.title + ' ' + result.content)
        from .evidence_policy import official_catalog, evidence_category
        needs_catalog = official_catalog(result.url) and evidence_category(query)=='release'
        if result.date_conflict:
            count('candidate_decisions', 'conflicting_search_dates')
            continue
        if result.published_at and not needs_catalog and not topic and not needs_fact_body:
            count('candidate_decisions', 'search_date_sufficient')
            continue
        if not official and len(terms & words) < min(2, len(terms)):
            count('candidate_decisions', 'irrelevant')
            continue
        if re.search(r'\b(?:shop now|buy now|discount codes?|sponsored|special offers?)\b', result.content, re.I):
            count('candidate_decisions', 'promotion')
            continue
        with _LOCK:
            cached = _CACHE.get(cache_key)
        if blocked and not (cached and cached[0] > time.monotonic()):
            count('candidate_decisions', 'host_backoff')
            enriched[index] = replace(result, accessibility='unavailable')
            continue
        if cached and cached[0] > time.monotonic():
            metadata = cached[1]
            count('fetch_outcomes', 'metadata_cache_hit')
            if len(metadata) > 8 and metadata[3] == 'available':
                try:
                    _check_partial(result, metadata[8], metadata[0], metadata[2], query, topic, needs_catalog)
                except ValueError as failure:
                    count('fetch_failures', fetch_failure_reason(failure))
                    enriched[index] = replace(result, accessibility='unavailable', partial_response=True)
                    continue
        else:
            if attempts >= 4 or attempt_budget[0] <= 0:
                count('candidate_decisions', 'attempt_limit')
                continue
            if time.monotonic() >= deadline:
                count('candidate_decisions', 'deadline')
                continue
            if not _SLOT.acquire(blocking=False):
                count('candidate_decisions', 'fetch_busy')
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
                attempt_budget[0] -= 1
                count('fetch_candidates', kind)
                fetched = fetch_metadata(result.url, min(deadline, time.monotonic()+(1.25 if needs_catalog or topic else .65)))
                if len(fetched) > 5:
                    _check_partial(result, fetched[5], fetched[0], fetched[2], query, topic, needs_catalog)
                metadata = (*fetched[:3], 'available', *(fetched[3:5] if len(fetched)>3 else (None,'')),
                            datetime.now(timezone.utc), None, fetched[5] if len(fetched)>5 else {})
                count('fetch_outcomes', 'partial_response' if len(fetched)>5 and fetched[5].get('partial_response') else 'complete_response')
            except (OSError, ValueError, http.client.HTTPException, RecursionError) as failure:
                if isinstance(failure, PageUnavailable) and failure.status in (403,429):
                    with _LOCK:
                        _HOST_BACKOFF[host] = time.monotonic()+60
                        while len(_HOST_BACKOFF) > 32:
                            _HOST_BACKOFF.popitem(last=False)
                metadata = (None, None, False, 'unavailable', None, '', None,
                            failure.status if isinstance(failure, PageUnavailable) else None,
                            {'failure_reason': fetch_failure_reason(failure)})
            finally:
                if guard is not None:
                    os.close(guard)
                _SLOT.release()
            with _LOCK:
                _CACHE[cache_key] = (time.monotonic() + (300 if metadata[0] or len(metadata)>4 and metadata[4] or len(metadata)>8 and metadata[8].get('text') else 15), metadata)
                _CACHE.move_to_end(cache_key)
                while len(_CACHE) > 64:
                    _CACHE.popitem(last=False)
        if len(metadata) > 8 and metadata[8].get('failure_reason'):
            count('fetch_failures', metadata[8]['failure_reason'])
        published, modified, conflict = metadata[:3]
        accessibility = metadata[3] if len(metadata) > 3 else 'available'
        enriched[index] = replace(result, published_at=published, modified_at=modified or result.modified_at, publication_source='article_metadata' if published else '', date_conflict=conflict, accessibility=accessibility, release_at=metadata[4] if len(metadata)>4 else None, release_evidence=metadata[5] if len(metadata)>5 else '', verified_at=metadata[6] if len(metadata)>6 else None)
        if len(metadata) > 7:
            enriched[index] = replace(enriched[index], http_status=metadata[7])
        if accessibility == 'available' and needs_catalog and enriched[index].release_evidence:
            enriched[index] = replace(enriched[index], evidence_kind='official_release_catalog',
                verified_url=metadata[8].get('url', result.url) if len(metadata)>8 else result.url)
        if not needs_catalog and accessibility == 'available' and (topic or len(metadata) > 8 and metadata[8]):
            page = metadata[8] if len(metadata) > 8 else {}
            # Validate the final redirected URL's authority, not only the search URL.
            final_url = page.get('url', '')
            claim, status = page_evidence(page.get('text', ''), final_url, query)
            mismatch = bool(published and result.published_at and published.date() != result.published_at.date())
            enriched[index] = replace(enriched[index], content=claim, verified_url=final_url,
                published_at=published or result.published_at,
                publication_source='article_metadata' if published else result.publication_source,
                date_conflict=conflict or mismatch, event_at=page.get('event_at'), entertainment_status=status,
                claim_status=classify_claims(claim) if claim and topic != 'officeholder' else '',
                partial_response=page.get('partial_response') is True,
                evidence_kind=('official_current_state' if official_office_page(final_url, query)
                               else 'fetched_report' if topic == 'entertainment' else 'fetched_reference') if claim else '')
    logger.info('Article candidate prioritization', extra={'article_candidates': {key: dict(values) for key, values in diagnostics.items()}})
    return enriched

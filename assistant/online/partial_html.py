"""Conservative article islands in a byte-bounded, incomplete HTML response.

This is not a document-completeness detector. Only a closed article in a closed,
page-bound metadata context can be returned; unread content remains unknown.
"""
import codecs
import json
import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .dates import consistent_date, parse_publication_date

MAX_TOKENS = 8000
MAX_DEPTH = 64
MAX_TEXT = 12000
MAX_JSON = 32768
_VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
_HIDDEN = {'script', 'style', 'template', 'nav', 'header', 'footer', 'head', 'title', 'iframe', 'aside'}
_ARTICLE_TYPES = {'Article', 'NewsArticle', 'BlogPosting', 'TechArticle', 'Report'}


class PartialEvidenceError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__('Bounded partial article rejected')


def _same_page(value, url):
    if not isinstance(value, str):
        return False
    candidate, page = urlsplit(urljoin(url, value)), urlsplit(url)
    return (candidate.scheme, candidate.netloc, candidate.path.rstrip('/'), candidate.query) == (
        page.scheme, page.netloc, page.path.rstrip('/'), page.query)


class _ArticleIsland(HTMLParser):
    def __init__(self, url, deadline):
        super().__init__(convert_charrefs=True)
        self.url, self.deadline = url, deadline
        self.stack, self.parts = [], []
        self.tokens = self.text_chars = self.json_chars = 0
        self.body_blocks = 0
        self.block_chars = 0
        self.head_closed = self.article_closed = False
        self.head_count = self.article_count = 0
        self.identities, self.published, self.modified, self.bound_dates = [], [], [], []
        self.json_parts = None

    def _tick(self):
        self.tokens += 1
        if self.tokens > MAX_TOKENS:
            raise PartialEvidenceError('partial_token_limit')
        if self.tokens % 128 == 0 and time.monotonic() >= self.deadline:
            raise TimeoutError('Article parsing deadline exhausted')

    def _inside(self, name):
        return any(tag == name for tag, _ in self.stack)

    def handle_starttag(self, tag, attrs):
        self._tick()
        attrs = dict(attrs)
        if tag == 'head':
            self.head_count += 1
        if tag == 'article':
            self.article_count += 1
            if not self.head_closed or self.article_count != 1:
                raise PartialEvidenceError('partial_article_scope')
        if tag == 'meta' and attrs.get('http-equiv', '').lower() == 'refresh':
            raise PartialEvidenceError('html_redirect')
        # Inspect every metadata declaration in the bounded prefix, even after
        # the article closes. Only explicitly matching JSON Article nodes bind.
        if tag == 'meta':
            key = (attrs.get('property') or attrs.get('name') or attrs.get('itemprop') or '').lower()
            if attrs.get('http-equiv', '').lower() == 'refresh':
                raise PartialEvidenceError('html_redirect')
            if key in {'article:published_time', 'og:published_time', 'datepublished', 'pubdate', 'publishdate', 'publish-date', 'publication_date', 'citation_publication_date', 'citation_date', 'dc.date.issued', 'dcterms.issued', 'parsely-pub-date', 'sailthru.date'}:
                self.published.append(attrs.get('content', ''))
            if key in {'article:modified_time', 'datemodified', 'last-modified'}:
                self.modified.append(attrs.get('content', ''))
            if key == 'og:url':
                self.identities.append(attrs.get('content', ''))
        if tag == 'link' and 'canonical' in attrs.get('rel', '').lower().split():
            self.identities.append(attrs.get('href', ''))
        if tag == 'time' and attrs.get('itemprop', '').lower() == 'datepublished':
            self.published.append(attrs.get('datetime', ''))
        if tag == 'script' and attrs.get('type', '').lower() == 'application/ld+json':
            self.json_parts, self.json_chars = [], 0
        hidden = (tag in _HIDDEN or attrs.get('aria-hidden') == 'true'
                  or bool(re.search(r'(?:^|[ ;])display\s*:\s*none', attrs.get('style', ''), re.I))
                  or bool({'ad', 'ads', 'advertisement', 'advertising', 'related', 'related-posts',
                           'related-articles', 'recommended', 'recommendations', 'comments', 'share', 'social'}
                          & set((attrs.get('class', '')+' '+attrs.get('role', '')).lower().split())))
        if tag not in _VOID:
            self.stack.append((tag, hidden))
            if len(self.stack) > MAX_DEPTH:
                raise PartialEvidenceError('partial_depth_limit')
        if self._inside('article') and tag in {'p', 'li'}:
            self.block_chars = 0
        if self._inside('article') and tag in {'p', 'div', 'h1', 'h2', 'h3', 'li', 'tr', 'br'}:
            self.parts.append('\n')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        self._tick()
        if tag in _VOID:
            return
        if not self.stack or self.stack[-1][0] != tag:
            raise PartialEvidenceError('partial_unbalanced_markup')
        if tag == 'script' and self.json_parts is not None:
            self._json_article(''.join(self.json_parts))
            self.json_parts = None
        if self._inside('article') and tag in {'p', 'div', 'h1', 'h2', 'h3', 'li', 'tr'}:
            self.parts.append('\n')
        if self._inside('article') and tag in {'p', 'li'} and self.block_chars >= 20 and not any(hidden for _, hidden in self.stack):
            self.body_blocks += 1
        self.stack.pop()
        if tag == 'head':
            self.head_closed = True
        if tag == 'article':
            self.article_closed = True

    def handle_data(self, data):
        self._tick()
        if not any(hidden for _, hidden in self.stack) and re.search(
                r'^(?:verify (?:that )?you are human|access denied|enable javascript and cookies|'
                r'subscribe to (?:continue|read)|sign in to continue|this article is (?:only )?for subscribers)\b',
                data.strip(), re.I):
            raise PartialEvidenceError('partial_restricted_page')
        if self.json_parts is not None:
            self.json_chars += len(data)
            if self.json_chars > MAX_JSON:
                raise PartialEvidenceError('partial_json_limit')
            self.json_parts.append(data)
        if self._inside('article') and not any(hidden for _, hidden in self.stack):
            self.text_chars += len(data)
            if self.text_chars > MAX_TEXT:
                raise PartialEvidenceError('partial_text_limit')
            self.parts.append(data)
            if self._inside('p') or self._inside('li'):
                self.block_chars += len(data.strip())

    def _json_article(self, value):
        try:
            pending = [json.loads(value)]
            for _ in range(100):
                if not pending:
                    break
                node = pending.pop()
                if isinstance(node, list):
                    if len(node) > 100:
                        raise PartialEvidenceError('partial_json_nodes')
                    pending.extend(node)
                elif isinstance(node, dict):
                    kinds = node.get('@type', [])
                    kinds = [kinds] if isinstance(kinds, str) else kinds
                    if isinstance(kinds, list) and any(kind in _ARTICLE_TYPES for kind in kinds if isinstance(kind, str)):
                        identities = [node[key] for key in ('url', 'mainEntityOfPage') if node.get(key)]
                        identities = [identity.get('@id') if isinstance(identity, dict) else identity for identity in identities]
                        if identities and all(_same_page(identity, self.url) for identity in identities):
                            self.bound_dates.append(node.get('datePublished'))
                            self.published.append(node.get('datePublished'))
                            if node.get('dateModified'):
                                self.modified.append(node['dateModified'])
                    for key in ('@graph', 'mainEntity'):
                        if key in node:
                            pending.append(node[key])
            if pending:
                raise PartialEvidenceError('partial_json_nodes')
        except (ValueError, TypeError, RecursionError) as failure:
            if isinstance(failure, PartialEvidenceError):
                raise
            raise PartialEvidenceError('partial_invalid_json') from failure

    def evidence(self):
        if not self.head_closed or self.head_count != 1 or not self.article_closed or self.article_count != 1:
            raise PartialEvidenceError('partial_incomplete_scope')
        if self._inside('head') or self._inside('article') or self.json_parts is not None:
            raise PartialEvidenceError('partial_incomplete_scope')
        if not self.identities or not all(_same_page(value, self.url) for value in self.identities):
            raise PartialEvidenceError('partial_page_identity')
        if len(self.bound_dates) > 1:
            raise PartialEvidenceError('partial_metadata_ambiguous')
        if not self.bound_dates or any(parse_publication_date(value) is None for value in self.published):
            raise PartialEvidenceError('partial_publication_missing')
        text = '\n'.join(clean for line in ''.join(self.parts).splitlines() if (clean := ' '.join(line.split())))
        # Visible publication labels are additional constraints, not substitutes
        # for the explicitly page-bound Article datePublished.
        for value in re.findall(r'(?im)^Published(?: on)?\s*:?\s*(20\d{2}-\d{2}-\d{2}|\d{1,2} [A-Za-z]+ 20\d{2}|[A-Za-z]+ \d{1,2},? 20\d{2})\b', text[:1200]):
            self.published.append(value)
        published = consistent_date(self.published)
        if published is None:
            raise PartialEvidenceError('partial_publication_conflict')
        if len(text) < 20 or not self.body_blocks:
            raise PartialEvidenceError('partial_text_missing')
        return published, consistent_date(self.modified), False, None, '', {
            'text': text, 'url': self.url, 'partial_response': True,
            'partial_scope': 'closed_article', 'event_at': None,
        }


def extract_partial_article(prefix, url, deadline):
    if urlsplit(url).scheme != 'https':
        raise PartialEvidenceError('partial_requires_https')
    parser = _ArticleIsland(url, deadline)
    decoder = codecs.getincrementaldecoder('utf-8')(errors='strict')
    try:
        for offset in range(0, len(prefix), 8192):
            if time.monotonic() >= deadline:
                raise TimeoutError('Article parsing deadline exhausted')
            parser.feed(decoder.decode(prefix[offset:offset+8192], final=False))
        # Never close()/flush an incomplete document: buffered tags and partial
        # entities must not become visible claim text.
        value = parser.evidence()
    except UnicodeError as failure:
        raise PartialEvidenceError('partial_invalid_encoding') from failure
    if time.monotonic() >= deadline:
        raise TimeoutError('Article parsing deadline exhausted')
    return value

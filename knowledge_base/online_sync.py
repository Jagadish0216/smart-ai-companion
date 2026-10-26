"""Bounded SearXNG snippet synchronization into the existing document store."""

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
import hashlib
import ipaddress
import logging
import math
import json
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from assistant.online.base import OnlineRetrievalResult
from assistant.online.factory import get_online_retriever
from assistant.online.searxng import _bounded_query, _normalize_hostname, _plain_text, _safe_public_url
from .models import Document
from .services import KnowledgeIngestionService, cleanup_stored_file

logger = logging.getLogger(__name__)


def _enabled(value):
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


@dataclass(frozen=True)
class OnlineSyncConfig:
    enabled: bool = True
    auto_persist: bool = True
    ttl_hours: float = 24.0
    max_queries: int = 20
    max_results: int = 4

    @classmethod
    def from_settings(cls):
        if not _enabled(getattr(settings, 'ONLINE_KNOWLEDGE_SYNC_ENABLED', True)):
            return cls(enabled=False)
        try:
            config = cls(
                enabled=_enabled(getattr(settings, 'ONLINE_KNOWLEDGE_SYNC_ENABLED', True)),
                auto_persist=_enabled(getattr(settings, 'ONLINE_KNOWLEDGE_AUTO_PERSIST', True)),
                ttl_hours=float(getattr(settings, 'ONLINE_KNOWLEDGE_TTL_HOURS', 24)),
                max_queries=int(str(getattr(settings, 'ONLINE_KNOWLEDGE_SYNC_MAX_QUERIES', 20))),
                max_results=int(str(getattr(settings, 'ONLINE_MAX_RESULTS', 4))),
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError('Online knowledge TTL must be numeric; query and result limits must be integers.') from exc
        config.validate()
        return config

    def validate(self):
        if not self.enabled:
            return
        if not math.isfinite(self.ttl_hours) or not 0 < self.ttl_hours <= 8760:
            raise ValueError('ONLINE_KNOWLEDGE_TTL_HOURS must be finite, greater than 0 and at most 8760.')
        if type(self.max_queries) is not int or not 1 <= self.max_queries <= 100:
            raise ValueError('ONLINE_KNOWLEDGE_SYNC_MAX_QUERIES must be between 1 and 100.')
        if type(self.max_results) is not int or not 1 <= self.max_results <= 20:
            raise ValueError('ONLINE_MAX_RESULTS must be between 1 and 20.')


@dataclass
class SyncResult:
    queries: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0
    disabled: bool = False

    def as_dict(self):
        return asdict(self)

    def merge(self, other):
        for field in ('queries', 'created', 'updated', 'unchanged', 'failed'):
            setattr(self, field, getattr(self, field) + getattr(other, field))


@dataclass
class SyncGroup:
    provider: str
    query: str
    document_ids: list


def normalized_query(value):
    return _bounded_query(value.casefold()) if isinstance(value, str) else ''


def canonical_source_url(value):
    """Reuse retrieval validation; normalize host/port without DNS or HTTP."""
    value = _safe_public_url(value)
    if not value or len(value) > 2048 or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        return ''
    try:
        parsed = urlsplit(value)
        host = _normalize_hostname(parsed.hostname)
        if host == 'localhost' or host.endswith(('.localhost', '.local')):
            return ''
        try:
            if not ipaddress.ip_address(host).is_global:
                return ''
        except ValueError:
            pass
        netloc = f'[{host}]' if ':' in host else host
        if parsed.port and (parsed.scheme, parsed.port) not in {('http', 80), ('https', 443)}:
            netloc += f':{parsed.port}'
        return urlunsplit((parsed.scheme, netloc, parsed.path or '/', parsed.query, ''))
    except (ValueError, UnicodeError, TypeError):
        return ''


def validated_result(value):
    if not isinstance(value, OnlineRetrievalResult) or value.provider != 'searxng':
        return None
    if not isinstance(value.retrieved_at, datetime) or timezone.is_naive(value.retrieved_at):
        return None
    for stamp in (value.published_at, value.modified_at, value.verified_at, value.release_at, value.event_at):
        if stamp is not None and (not isinstance(stamp, datetime) or timezone.is_naive(stamp)):
            return None
    url = canonical_source_url(value.url)
    title = _plain_text(value.title[:2048])[:255] if isinstance(value.title, str) else ''
    snippet = _plain_text(value.content[:16000])[:6000] if isinstance(value.content, str) else ''
    # Ingestion accepts UTF-8 text only. Reject embedded binary/control data.
    if not url or not title or not snippet or any(ord(char) < 32 for char in title + snippet):
        return None
    return replace(value, title=title, url=url, content=snippet)


def source_identity(provider, url):
    digest = hashlib.sha256(f'{provider}\0{url}'.encode('utf-8')).hexdigest()
    return f'online:{provider}:{digest}', f'online-{digest[:24]}.md'


def source_content(result):
    # Legacy technical snippets retain their content-only hash. Verified page
    # snapshots also save auditable provenance in the existing sync file; cache
    # reads never rewrite it or manufacture a new verification time.
    provenance = ''
    if result.evidence_kind and result.verified_at:
        data = result.source_metadata()
        provenance = 'Verified evidence: ' + json.dumps(data, sort_keys=True) + '\n'
    return f'Title: {result.title}\nSource: {result.url}\n{provenance}\nSnippet:\n{result.content}\n'


class OnlineKnowledgeSyncService:
    def __init__(self, *, config=None, retriever=None):
        self.config = config if config is not None else OnlineSyncConfig.from_settings()
        self.config.validate()
        self.retriever = retriever

    def _set_error(self, ids, message):
        if not ids:
            return
        try:
            Document.objects.filter(pk__in=ids, source_type=Document.SourceType.ONLINE).update(sync_error_message=message[:255])
        except DatabaseError:
            logger.warning('Online knowledge sync error state could not be stored')

    def _upsert(self, query, result):
        identity, filename = source_identity(result.provider, result.url)
        content = source_content(result)
        digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
        lookup = dict(source_type=Document.SourceType.ONLINE, provider=result.provider, source_url=result.url)
        # The conditional DB constraint is the final concurrent-insert guard.
        # Retry only an integrity race, once, after its transaction rolls back.
        for attempt in range(2):
            new_name = ''
            storage = Document._meta.get_field('file').storage
            try:
                with transaction.atomic():
                    from django.db.models import Sum
                    from django.db.models.functions import Length
                    from .models import EvidenceVersion
                    size = EvidenceVersion.objects.aggregate(size=Sum(Length('content')))['size'] or 0
                    limit = max(1024, min(1024**3, int(getattr(settings, 'KNOWLEDGE_ARCHIVE_MAX_BYTES', 128*1024**2))))
                    if size + len(result.content.encode('utf-8')) > limit:
                        raise ValueError('Evidence archive capacity reached; retained evidence is not deleted.')
                    document = Document.objects.select_for_update().filter(**lookup).first()
                    created = document is None
                    if created:
                        document = Document(**lookup, source_identifier=identity)
                    elif document.retrieved_at and result.retrieved_at < document.retrieved_at:
                        return 'unchanged'  # A delayed response must not overwrite newer knowledge.
                    changed = document.content_hash != digest or document.status != 'INDEXED'
                    if changed:
                        document = KnowledgeIngestionService().ingest_text(
                            content, filename=filename, file_type='text/markdown',
                            source_identifier=identity, document=document,
                        ).document
                        new_name = document.file.name
                    document.source_title = result.title
                    document.sync_query = query
                    document.retrieved_at = result.retrieved_at
                    document.last_synced_at = result.verified_at or timezone.now()
                    document.expires_at = result.retrieved_at + timedelta(hours=self.config.ttl_hours)
                    if result.evidence_kind and result.verified_at:
                        document.expires_at = min(document.expires_at, result.verified_at + timedelta(minutes=5))
                    document.content_hash = digest
                    document.sync_error_message = ''
                    document.save(update_fields=('source_title', 'sync_query', 'retrieved_at', 'last_synced_at',
                                                 'expires_at', 'content_hash', 'sync_error_message'))
                    from .archive import record_evidence
                    record_evidence(document, query, result)
                return 'created' if created else 'updated' if changed else 'unchanged'
            except BaseException as exc:
                if new_name:
                    cleanup_stored_file(storage, new_name)
                if isinstance(exc, IntegrityError) and attempt == 0:
                    continue
                raise

    def persist_results(self, query, results):
        counts = SyncResult(disabled=not self.config.enabled)
        if counts.disabled:
            return counts
        query = normalized_query(query)
        if not query or not isinstance(results, (list, tuple)):
            counts.failed = 1
            return counts
        seen = set()
        for raw in results[:self.config.max_results]:
            result = validated_result(raw)
            if result is None:
                counts.failed += 1
                continue
            from assistant.online.grounding import select_current_evidence, has_conflicting_claims
            from assistant.online.evidence_policy import evidence_category
            from assistant.routing import QueryRouter
            if QueryRouter.requires_online(query) and evidence_category(query) != 'technical':
                if (not result.verified_at or result.accessibility != 'available'
                        or not select_current_evidence([result], query, timezone.now())
                        or has_conflicting_claims([r for r in results if isinstance(r, OnlineRetrievalResult)], query)):
                    counts.failed += 1
                    continue
            key = (result.provider, result.url)
            if key in seen:
                continue
            seen.add(key)
            try:
                outcome = self._upsert(query, result)
                setattr(counts, outcome, getattr(counts, outcome) + 1)
            except Exception:
                counts.failed += 1
                try:
                    ids = Document.objects.filter(source_type='ONLINE', provider=result.provider, source_url=result.url).values_list('id', flat=True)
                    self._set_error(list(ids), 'Local knowledge refresh failed; last good content retained.')
                except DatabaseError:
                    logger.warning('Online knowledge sync error state could not be stored')
                logger.warning('Online knowledge persistence failed')
        return counts

    def _retrieve(self, query, provider, ids):
        if provider != str(getattr(settings, 'ONLINE_PROVIDER', 'searxng')).strip().lower():
            self._set_error(ids, 'Saved provider is not the configured online provider.')
            return None
        try:
            results = (self.retriever if self.retriever is not None else get_online_retriever()).retrieve(query)
            if not isinstance(results, (list, tuple)):
                raise ValueError('Malformed result list')
            return results[:self.config.max_results]
        except Exception:
            self._set_error(ids, 'Online provider unavailable or response invalid; last good content retained.')
            logger.warning('Online knowledge retrieval failed')
            return None

    def sync_query(self, query, *, provider=None, document_ids=None):
        if not self.config.enabled:
            return SyncResult(disabled=True)
        query = normalized_query(query)
        provider = provider or str(getattr(settings, 'ONLINE_PROVIDER', 'searxng')).strip().lower()
        if not query:
            return SyncResult(failed=1)
        if document_ids is None:
            document_ids = [doc.pk for doc in Document.objects.filter(source_type='ONLINE', provider=provider).only('id', 'sync_query')
                            if normalized_query(doc.sync_query) == query]
        results = self._retrieve(query, provider, document_ids)
        if results is None:
            return SyncResult(queries=1, failed=max(1, len(document_ids)))
        counts = self.persist_results(query, results)
        counts.queries = 1
        returned = {valid.url for value in results if (valid := validated_result(value)) is not None and valid.provider == provider}
        missing = list(Document.objects.filter(pk__in=document_ids).exclude(source_url__in=returned).values_list('id', flat=True))
        self._set_error(missing, 'Source not returned for the saved query; expiry was not extended.')
        counts.failed += len(missing)
        return counts

    def sync_document(self, document):
        if not self.config.enabled:
            return SyncResult(disabled=True)
        if document.source_type != Document.SourceType.ONLINE or not normalized_query(document.sync_query):
            return SyncResult(failed=1)
        results = self._retrieve(normalized_query(document.sync_query), document.provider, [document.pk])
        if results is None:
            return SyncResult(queries=1, failed=1)
        matching = [valid for value in results if (valid := validated_result(value)) is not None
                    and valid.provider == document.provider and valid.url == document.source_url]
        if not matching:
            self._set_error([document.pk], 'Source not returned for the saved query; expiry was not extended.')
            return SyncResult(queries=1, failed=1)
        counts = self.persist_results(document.sync_query, matching[:1])
        counts.queries = 1
        return counts

    def plan(self, *, all_sources=False):
        if not self.config.enabled:
            return []
        sources = Document.objects.filter(source_type='ONLINE')
        if not all_sources:
            sources = sources.filter(Q(expires_at__lte=timezone.now()) | Q(expires_at__isnull=True))
        sources = sources.only('id', 'provider', 'sync_query', 'last_synced_at', 'expires_at').order_by(
            F('last_synced_at').asc(nulls_first=True), F('expires_at').asc(nulls_first=True), 'id',
        )
        groups = {}
        for document in sources.iterator(chunk_size=200):
            query = normalized_query(document.sync_query)
            key = (document.provider, query)
            if not query or not document.provider:
                continue
            if key not in groups:
                if len(groups) >= self.config.max_queries:
                    continue
                groups[key] = SyncGroup(document.provider, query, [])
            groups[key].document_ids.append(document.pk)
        return list(groups.values())

    def sync_stale(self, *, all_sources=False, dry_run=False):
        counts = SyncResult(disabled=not self.config.enabled)
        groups = self.plan(all_sources=all_sources)
        if dry_run:
            counts.queries = len(groups)
            return counts
        for group in groups:
            counts.merge(self.sync_query(group.query, provider=group.provider, document_ids=group.document_ids))
        return counts


def persist_used_online_results(query, results):
    """Answer-path hook: local work only, and all failures stay outside the answer."""
    if not _enabled(getattr(settings, 'ONLINE_KNOWLEDGE_SYNC_ENABLED', True)) or not _enabled(
        getattr(settings, 'ONLINE_KNOWLEDGE_AUTO_PERSIST', True)
    ):
        return
    try:
        OnlineKnowledgeSyncService().persist_results(query, results)
    except Exception:
        logger.warning('Automatic online knowledge persistence unavailable')


def load_verified_current(query):
    """Exact-query, unexpired SQLite cache. Old snippets never become verified pages."""
    from assistant.online.evidence_policy import evidence_category
    from assistant.online.dates import parse_publication_date
    from assistant.online.grounding import select_current_evidence, has_conflicting_claims
    documents = Document.objects.filter(source_type='ONLINE', status='INDEXED',
        sync_query=normalized_query(query), expires_at__gt=timezone.now()).order_by('-last_synced_at')[:4]
    values = []
    for doc in documents:
        try:
            # The existing local sync file preserves provenance across lexical
            # chunk boundaries. Bound reads and check the saved digest.
            with doc.file.open('rb') as saved:
                payload = saved.read(16001)
            if len(payload) > 16000 or hashlib.sha256(payload).hexdigest() != doc.content_hash:
                continue
            head, content = payload.decode('utf-8').split('\nSnippet:\n', 1)
            if not 20 <= len(content.strip()) <= 1200:
                continue
            record = next(line[len('Verified evidence: '):] for line in head.splitlines() if line.startswith('Verified evidence: '))
            data = json.loads(record)
            if data.get('url') != doc.source_url or data.get('provider') != doc.provider:
                continue
            values.append(OnlineRetrievalResult(title=doc.source_title, url=doc.source_url,
                content=content.strip(), provider=doc.provider, retrieved_at=doc.retrieved_at,
                published_at=parse_publication_date(data.get('published_at')),
                modified_at=parse_publication_date(data.get('modified_at')),
                verified_at=parse_publication_date(data.get('verified_at')),
                event_at=parse_publication_date(data.get('event_at')),
                release_at=parse_publication_date(data.get('release_at')),
                release_evidence=content.strip() if data.get('evidence_kind') == 'official_release_catalog' else '',
                verified_url=data.get('verified_url', ''), evidence_kind=data.get('evidence_kind', ''),
                entertainment_status=data.get('entertainment_status', ''),
                claim_status=data.get('claim_status', ''),
                partial_response=data.get('partial_response') is True,
                publication_source=data.get('publication_source', ''), accessibility='available'))
        except (OSError, ValueError, TypeError, AttributeError, StopIteration):
            continue
    if not values and not documents:
        from .archive import fresh_variations
        values = fresh_variations(query)
    accepted = select_current_evidence(values, query, timezone.now())
    return [] if has_conflicting_claims(accepted, query) else accepted

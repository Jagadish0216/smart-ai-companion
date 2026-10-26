"""Network-free versioned evidence storage and conservative historical retrieval."""
import hashlib
import re
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .models import EvidenceVersion, EvidenceQuery
from .retrieval import _tokenize


def record_evidence(document, query, result):
    # The caller owns the transaction. Never infer verification from a download.
    verified = bool(result.verified_at and result.evidence_kind and
                    result.accessibility == 'available' and not result.date_conflict)
    digest = hashlib.sha256(result.content.encode('utf-8')).hexdigest()
    version, _ = EvidenceVersion.objects.get_or_create(
        document=document, content_hash=digest, retrieved_at=result.retrieved_at,
        defaults=dict(content=result.content[:6000], verified_at=result.verified_at,
                      published_at=result.published_at, expires_at=document.expires_at,
                      metadata=result.source_metadata(), state='VERIFIED' if verified else 'UNVERIFIED'))
    if verified:
        # Different text from the same source is a replacement, not a consensus.
        EvidenceVersion.objects.filter(document=document, state='VERIFIED').exclude(pk=version.pk).exclude(
            content_hash=digest).update(state='SUPERSEDED')
    EvidenceQuery.objects.get_or_create(evidence=version, query=query[:600])


def historical_results(query, *, conversation=None, limit=3):
    terms = set(_tokenize(query)) - {'yesterday', 'checked', 'check', 'now', 'working', 'currently', 'offline', 'only'}
    # Resolve a pronoun using the last user question, never model assertions.
    if re.search(r'\b(?:he|she|it|they)\b', query, re.I) and conversation:
        previous = conversation.messages.filter(sender='USER').exclude(text=query).order_by('-id').first()
        if previous:
            terms |= set(_tokenize(previous.text)) - {'now', 'currently', 'working'}
    terms -= {'he', 'she', 'they', 'his', 'her'}
    if not terms:
        return []
    candidates = Q()
    for term in sorted(terms)[:24]:
        candidates |= Q(content__icontains=term) | Q(queries__query__icontains=term)
    versions = EvidenceVersion.objects.filter(state__in=['VERIFIED', 'SUPERSEDED']).filter(candidates)
    if 'yesterday' in query.lower():
        yesterday = timezone.localdate() - timedelta(days=1)
        versions = versions.filter(verified_at__date=yesterday)
    ranked = []
    for version in versions.select_related('document').distinct().order_by('-verified_at')[:500]:
        if hashlib.sha256(version.content.encode('utf-8')).hexdigest() != version.content_hash:
            continue
        words = set(_tokenize(version.content + ' ' + version.document.source_title))
        matched = len(terms & words)
        if matched < min(2, len(terms)):
            continue
        ranked.append((matched / len(terms), version))
    ranked.sort(key=lambda pair: (-pair[0], -(pair[1].verified_at.timestamp() if pair[1].verified_at else 0)))
    selected, seen = [], set()
    for _, version in ranked:
        key = (version.document_id, version.content_hash)
        if key in seen:
            continue
        seen.add(key)
        selected.append(version)
        if len(selected) >= limit:
            break
    return selected


def fresh_variations(query):
    """Bounded semantic/lexical cache; same admission policy as live evidence."""
    from assistant.online.base import OnlineRetrievalResult
    from assistant.online.dates import parse_publication_date
    values = []
    for v in historical_results(query, limit=4):
        if v.freshness != 'FRESH' or not v.document.expires_at or v.document.expires_at <= timezone.now():
            continue
        data = v.metadata
        fields = {key: data[key] for key in ('evidence_kind', 'verified_url', 'entertainment_status', 'claim_status', 'publication_source', 'partial_response') if key in data}
        values.append(OnlineRetrievalResult(title=v.document.source_title, url=v.document.source_url,
            content=v.content, provider=v.document.provider, retrieved_at=v.retrieved_at,
            verified_at=v.verified_at, published_at=v.published_at, accessibility='available',
            modified_at=parse_publication_date(data.get('modified_at')),
            release_at=parse_publication_date(data.get('release_at')),
            event_at=parse_publication_date(data.get('event_at')),
            release_evidence=v.content if data.get('evidence_kind') == 'official_release_catalog' else '', **fields))
    return values


def historical_answer(query, *, conversation=None):
    versions = historical_results(query, conversation=conversation)
    if not versions:
        return None
    # Quote source evidence without synthesizing a new current claim. Differing
    # reports stay separate; the existing conflict detector adds an explicit flag.
    from assistant.online.base import OnlineRetrievalResult
    from assistant.online.grounding import has_conflicting_claims
    comparable = [OnlineRetrievalResult(title=v.document.source_title, url=v.document.source_url,
                  content=v.content, provider=v.document.provider, retrieved_at=v.retrieved_at) for v in versions]
    conflict = has_conflicting_claims(comparable, query)
    heading = ('Stored sources disagree; no reliable current conclusion can be drawn.\n' if conflict else '')
    heading += 'Previously verified source evidence, not checked live (not a live check) and possibly outdated:\n'
    blocks = []
    for v in versions:
        text = v.content
        if len(text) > 1200:
            text = text[:1200].rsplit(' ', 1)[0] + ' [excerpt; consult source for full context]'
        blocks.append(f'[{v.freshness}; last verified {v.verified_at.isoformat()}] '
                      f'{v.document.source_title}: {text}\nSource: {v.document.source_url}')
    now = timezone.now()
    EvidenceVersion.objects.filter(pk__in=[v.pk for v in versions]).update(last_used_at=now)
    if conversation:
        for v in versions:
            EvidenceQuery.objects.get_or_create(evidence=v, query=query.casefold()[:600], defaults={'conversation': conversation})
    return heading + '\n\n'.join(blocks), [dict(v.metadata, freshness=v.freshness, last_used_at=now.isoformat()) for v in versions]

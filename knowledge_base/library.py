"""Resumable, hash-pinned reference ingestion; no inference or implicit crawling."""
import hashlib
import json
import shutil
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from .models import LibrarySource
from .services import KnowledgeIngestionService


def manifest():
    return json.loads((Path(settings.BASE_DIR) / 'knowledge_library/manifest.json').read_text(encoding='utf-8'))


def initialize_library(*, max_sources=20):
    data = manifest()
    root = Path(settings.BASE_DIR) / 'knowledge_library'
    if shutil.disk_usage(settings.BASE_DIR).free < 512 * 1024 * 1024:
        raise ValueError('At least 512 MiB free disk is required for reference ingestion.')
    counts = {'indexed': 0, 'unchanged': 0, 'failed': 0, 'unpopulated': 0}
    attempts = 0
    for entry in data['sources']:
        source, _ = LibrarySource.objects.get_or_create(key=entry['key'], defaults={'manifest_version': data['version']})
        source.metadata = entry
        source.manifest_version = data['version']
        if entry['status'] != 'BUNDLED':
            counts['unpopulated'] += 1
            source.status = entry['status']
            source.save()
            continue
        if attempts >= min(max_sources, 20):
            continue
        attempts += 1
        source.last_attempt_at = timezone.now()
        try:
            path = (root / entry['path']).resolve()
            path.relative_to(root.resolve())
            if path.stat().st_size > 300000:
                raise ValueError('Reference exceeds per-source size limit.')
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != entry['sha256']:
                raise ValueError('Reference checksum mismatch.')
            license_path = (root / entry['license_file']).resolve()
            license_path.relative_to(root.resolve())
            if not license_path.is_file():
                raise ValueError('Attribution license missing.')
            if hashlib.sha256(license_path.read_bytes()).hexdigest() != entry['license_sha256']:
                raise ValueError('Attribution license checksum mismatch.')
            if source.document_id and source.document.content_hash == entry['sha256'] and source.document.status == 'INDEXED' and source.document.chunks.exists():
                counts['unchanged'] += 1
            else:
                text = f"{entry['title']}\nSource: {entry['url']}\nVersion: {entry['version']}\nAttribution: {entry['attribution']} ({entry['license']})\n\n" + content.decode('utf-8')
                result = KnowledgeIngestionService().ingest_text(text, filename=entry['key']+'.txt',
                    file_type='text/plain', source_identifier='reference:'+entry['key'])
                source.document = result.document
                source.document.content_hash = entry['sha256']
                source.document.source_title = entry['title']
                source.document.source_url = entry['url']
                source.document.save(update_fields=['content_hash', 'source_title', 'source_url'])
                counts['indexed'] += 1
            source.status = 'INDEXED'
            source.last_success_at = timezone.now()
            source.next_refresh_at = timezone.now() + timedelta(days=30)
            source.error = ''
        except Exception as exc:
            counts['failed'] += 1
            source.error = str(exc)[:255]
            source.status = 'ERROR'
        source.save()
    return dict(counts, version=data['version'], coverage=data['coverage_claim'])


def library_status():
    from django.db.models import Sum
    from .models import Document, EvidenceVersion
    sources = list(LibrarySource.objects.values('key', 'status', 'last_success_at', 'next_refresh_at', 'error'))
    return {'version': manifest()['version'], 'coverage': manifest()['coverage_claim'], 'sources': sources,
            'indexed_references': LibrarySource.objects.filter(document__status='INDEXED').count(),
            'saved_versions': EvidenceVersion.objects.count(),
            'fresh_versions': EvidenceVersion.objects.filter(state='VERIFIED', expires_at__gt=timezone.now()).count(),
            'document_bytes': Document.objects.aggregate(size=Sum('file_size'))['size'] or 0}

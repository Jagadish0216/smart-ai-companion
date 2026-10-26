"""Copy existing checksummed provenance, without inventing verification dates."""
import hashlib
import json
from django.db import migrations
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware


def preserve(apps, schema_editor):
    Document = apps.get_model('knowledge_base', 'Document')
    Version = apps.get_model('knowledge_base', 'EvidenceVersion')
    Query = apps.get_model('knowledge_base', 'EvidenceQuery')
    for doc in Document.objects.filter(source_type='ONLINE', status='INDEXED').iterator(chunk_size=100):
        try:
            with doc.file.open('rb') as stream:
                payload = stream.read(16001)
            if len(payload) > 16000 or hashlib.sha256(payload).hexdigest() != doc.content_hash:
                continue
            head, content = payload.decode('utf-8').split('\nSnippet:\n', 1)
            data = json.loads(next(line.removeprefix('Verified evidence: ') for line in head.splitlines() if line.startswith('Verified evidence: ')))
            stamp = parse_datetime(data.get('verified_at') or '')
            if (not stamp or not is_aware(stamp) or not data.get('evidence_kind') or
                    data.get('accessibility') != 'available' or data.get('url') != doc.source_url or not doc.retrieved_at):
                continue
            content = content.strip()[:6000]
            version, _ = Version.objects.get_or_create(document=doc, retrieved_at=doc.retrieved_at,
                content_hash=hashlib.sha256(content.encode()).hexdigest(), defaults=dict(content=content,
                verified_at=stamp, published_at=parse_datetime(data.get('published_at') or ''),
                expires_at=doc.expires_at, metadata=data, state='VERIFIED'))
            if doc.sync_query:
                Query.objects.get_or_create(evidence=version, query=doc.sync_query[:600])
        except (OSError, ValueError, TypeError, StopIteration):
            continue  # Original document is retained for inspection/reindexing.


class Migration(migrations.Migration):
    dependencies = [('knowledge_base', '0005_knowledgechunk_page_number_evidenceversion_and_more')]
    operations = [migrations.RunPython(preserve, migrations.RunPython.noop)]

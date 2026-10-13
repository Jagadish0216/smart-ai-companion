"""Explicit, bounded snippet refresh; optional systemd scheduling is external."""

from django.core.management.base import BaseCommand, CommandError

from knowledge_base.models import Document
from knowledge_base.online_sync import OnlineKnowledgeSyncService, SyncResult, normalized_query


class Command(BaseCommand):
    help = 'Refresh stale online knowledge from saved SearXNG queries; never fetch result URLs.'

    def add_arguments(self, parser):
        inputs = parser.add_mutually_exclusive_group()
        inputs.add_argument('--all', action='store_true', help='Refresh all known online query groups, subject to the configured limit.')
        inputs.add_argument('--document', type=int, help='Refresh one online source using its stored search query.')
        inputs.add_argument('--query', help='Search and synchronize one bounded query.')
        parser.add_argument('--dry-run', action='store_true', help='List selected queries without network or database/storage changes.')

    def handle(self, *args, **options):
        try:
            service = OnlineKnowledgeSyncService()
            if not service.config.enabled:
                result = SyncResult(disabled=True)
                self.stdout.write('Online knowledge synchronization disabled.')
            elif options.get('document') is not None:
                document = Document.objects.get(pk=options['document'], source_type='ONLINE')
                self.stdout.write(f'Selected online document {document.pk}.')
                result = SyncResult(queries=1) if options['dry_run'] else service.sync_document(document)
            elif options.get('query') is not None:
                query = normalized_query(options['query'])
                if not query:
                    raise CommandError('--query must not be empty.')
                self.stdout.write(f'Selected query: {query}')
                result = SyncResult(queries=1) if options['dry_run'] else service.sync_query(query)
            else:
                if options['dry_run']:
                    for group in service.plan(all_sources=options['all']):
                        self.stdout.write(f'{group.provider}: {group.query} ({len(group.document_ids)} source(s))')
                result = service.sync_stale(all_sources=options['all'], dry_run=options['dry_run'])
        except Document.DoesNotExist as exc:
            raise CommandError('Online document not found.') from exc
        except CommandError:
            raise
        except Exception as exc:
            raise CommandError('Knowledge synchronization could not run; check configuration and local database/storage.') from exc
        self.stdout.write(' '.join(f'{name}={getattr(result, name)}' for name in ('queries', 'created', 'updated', 'unchanged', 'failed')))

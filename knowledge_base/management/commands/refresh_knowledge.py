import json
from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from system.models import CompanionState, SystemSetting
from knowledge_base.online_sync import OnlineKnowledgeSyncService, OnlineSyncConfig
from knowledge_base.library import initialize_library


class Command(BaseCommand):
    help = 'One bounded, resource-aware refresh pass; no Ollama inference.'

    def handle(self, *args, **options):
        from system.control_plane.resource_manager import get_resource_manager_report
        report = get_resource_manager_report()
        if report.get('recommended_profile') not in {'PERFORMANCE', 'BALANCED'}:
            self.stdout.write('Deferred: resource conditions unavailable or constrained.')
            return
        if CompanionState.objects.filter(updated_at__gte=timezone.now()-timedelta(minutes=2)).exclude(state__in=['READY', 'IDLE']).exists():
            self.stdout.write('Deferred: voice interaction active.')
            return
        cadence = max(300, min(86400, int(getattr(settings, 'KNOWLEDGE_REFRESH_SECONDS', 3600))))
        last = SystemSetting.objects.filter(key='knowledge_next_refresh').first()
        if last and timezone.now().timestamp() < float(last.value):
            self.stdout.write('Deferred: refresh cadence not due.')
            return
        SystemSetting.objects.update_or_create(key='knowledge_next_refresh', defaults={'value': str(timezone.now().timestamp()+cadence)})
        config = OnlineSyncConfig.from_settings()
        from dataclasses import replace
        result = OnlineKnowledgeSyncService(config=replace(config, max_queries=min(3, config.max_queries))).sync_stale()
        library = initialize_library()
        SystemSetting.objects.update_or_create(key='knowledge_refresh_status', defaults={'value': 'failed' if result.failed or library['failed'] else 'success'})
        if not result.failed and not library['failed']:
            SystemSetting.objects.update_or_create(key='knowledge_last_success', defaults={'value': timezone.now().isoformat()})
        self.stdout.write(json.dumps({'online': result.as_dict(), 'library': library}))
        if result.failed or library['failed']:
            raise CommandError('Refresh incomplete; previous source data retained.')

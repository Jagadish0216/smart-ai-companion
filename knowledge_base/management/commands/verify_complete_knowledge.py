"""Read-only runtime checks; synthetic mutations live exclusively in tests."""
import json
import shutil
import subprocess
import time
from urllib.request import build_opener, ProxyHandler
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from assistant.online.factory import get_online_retriever
from assistant.online.grounding import select_current_evidence, has_conflicting_claims
from knowledge_base.models import LibrarySource


class Command(BaseCommand):
    help = 'Read-only live evidence, library, service and resource checks; no inference.'

    def handle(self, *args, **options):
        started = time.monotonic()
        report = {'synthetic': False, 'model_calls': 0}
        services = {}
        for name in ['smart-ai-companion.service', 'smart-ai-companion-voice.service', 'ollama.service']:
            try:
                services[name] = subprocess.run(['systemctl', 'is-active', '--quiet', name], timeout=5).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                services[name] = False
        report['services'] = services
        try:
            with build_opener(ProxyHandler({})).open('http://127.0.0.1:8000/api/knowledge/library/', timeout=5) as response:
                health = json.loads(response.read(65536))
            report['application_http'] = 'indexed_references' in health
        except Exception:
            report['application_http'] = False
        report['indexed_library_sources'] = LibrarySource.objects.filter(document__status='INDEXED', document__chunks__isnull=False).distinct().count()
        report['live_sources'] = []
        try:
            query = 'Who is the Chief Minister of Telangana?'
            results = get_online_retriever().retrieve(query)
            accepted = select_current_evidence(results, query, timezone.now())
            if not has_conflicting_claims(accepted, query):
                report['live_sources'] = [r.source_metadata() for r in accepted if r.verified_at and r.accessibility == 'available']
        except Exception as exc:
            report['live_failure'] = type(exc).__name__
        report['free_disk_bytes'] = shutil.disk_usage('.').free
        try:
            from system.control_plane.resource_manager import get_resource_manager_report
            resources = get_resource_manager_report()
            report['resource_profile'] = resources.get('recommended_profile')
            report['resource_health'] = resources.get('subsystem_health')
        except Exception:
            report['resource_profile'] = 'unavailable'
        report['elapsed_ms'] = round((time.monotonic()-started)*1000)
        report['pass'] = bool(all(services.values()) and report['application_http'] and report['indexed_library_sources'] and report['live_sources'])
        self.stdout.write(json.dumps(report, default=str, indent=2))
        if not report['pass']:
            raise CommandError('Runtime acceptance incomplete. See the consolidated report.')

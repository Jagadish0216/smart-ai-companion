"""Public acquisition diagnostics without generation, persistence or hardware."""
import json
import time
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from assistant.online.factory import get_online_retriever
from assistant.online.base import OnlineRetrievalError
from assistant.online.grounding import build_online_grounding_instruction, has_conflicting_claims, select_current_evidence


class Command(BaseCommand):
    help = 'Check public OS release/news evidence. JSONL; no model calls or knowledge writes.'

    def add_arguments(self, parser):
        parser.add_argument('--require-evidence', action='store_true')

    def handle(self, *args, **options):
        missing = False
        today = timezone.localdate().isoformat()
        for query in (f'Latest Raspberry Pi OS version as of {today}', 'Raspberry Pi news today'):
            started = time.perf_counter()
            row = {'question': query, 'provider_reachable': False, 'results_retrieved': 0,
                   'sources_verified': 0, 'sources_unavailable': 0, 'sources_access_denied': 0,
                   'fresh_evidence_accepted': 0, 'evidence_used_by_model': False, 'model_called': False,
                   'answerable_evidence': False, 'sources': []}
            try:
                retriever = get_online_retriever()
                results = retriever.retrieve(query)
                row.update(getattr(retriever, 'diagnostics', {}))
                row['provider_reachable'] = True
                fresh = select_current_evidence(results, query, timezone.now())
                row['fresh_evidence_accepted'] = len(fresh)
                conflict = has_conflicting_claims(fresh, query)
                _, accepted = build_online_grounding_instruction(fresh, query=query, request_time=timezone.now(), max_context_chars=3600)
                row['answerable_evidence'] = bool(accepted) and not conflict
                row['sources'] = [r.source_metadata() for r in accepted]
                row['fallback_reason'] = 'conflicting_evidence' if conflict else '' if accepted else 'no_relevant_current_results'
            except OnlineRetrievalError:
                row['fallback_reason'] = 'online_unavailable'
            row['search_ms'] = round((time.perf_counter()-started)*1000)
            missing |= not row['answerable_evidence']
            self.stdout.write(json.dumps(row))
        if missing and options['require_evidence']:
            raise CommandError('Current evidence insufficient; inspect the JSON diagnostics.')

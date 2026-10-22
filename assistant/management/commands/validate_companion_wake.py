"""Explicit offline evaluation. Produces no shipped model or default activation."""
import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from assistant.voice.wakeword.companion import candidate_config, evaluate


class Command(BaseCommand):
    help = 'Evaluate a trained Hey Companion model offline; no download, microphone or service changes.'

    def add_arguments(self, parser):
        parser.add_argument('--model', required=True, type=Path)
        parser.add_argument('--corpus', required=True, type=Path)
        parser.add_argument('--training-record', required=True, type=Path)
        parser.add_argument('--report', required=True, type=Path)
        parser.add_argument('--threshold', type=float, default=.6)
        parser.add_argument('--accept-pi', action='store_true', help='Attest that held-out Pi audio and supervised playback/cancellation/fallback/restart checks passed; only after physical validation.')

    def handle(self, *args, **options):
        try:
            report = evaluate(candidate_config(options['model'], options['threshold']), options['corpus'],
                              options['training_record'], accept_pi=options['accept_pi'])
            path = options['report']
            # Never overwrite a model, training record or prior approval accidentally.
            with path.open('x', encoding='utf-8') as output:
                path.chmod(0o600)
                json.dump(report, output, indent=2, allow_nan=False)
        except Exception as exc:
            raise CommandError(f'Validation failed; activation stays disabled: {exc}') from exc
        self.stdout.write(json.dumps(report['metrics']))
        self.stdout.write('Pi acceptance recorded.' if report['accepted_on_pi'] else 'NOT approved. Use GPIO17 push-to-talk.')

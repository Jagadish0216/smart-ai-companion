"""Manual maintenance-window benchmark; no database or hardware writes."""
import json
from django.core.management.base import BaseCommand, CommandError
from assistant.model_benchmark import BenchmarkError, run_benchmark


class Command(BaseCommand):
    help = 'Compare three installed models sequentially; requires inactive application/voice services. Emits JSONL.'

    def add_arguments(self, parser):
        parser.add_argument('--repeats', type=int, default=1)

    def handle(self, *args, **options):
        try:
            run_benchmark(lambda row: self.stdout.write(json.dumps(row)), options['repeats'])
        except BenchmarkError as exc:
            raise CommandError(str(exc)) from None
        except Exception as exc:
            # Do not expose arbitrary HTTP exception bodies or configured URLs.
            raise CommandError('Benchmark stopped safely (' + type(exc).__name__ + '); verify maintenance, resource and Ollama health.') from None

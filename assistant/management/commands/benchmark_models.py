"""Manual maintenance-window benchmark; no database or hardware writes."""
import json
import os
from contextlib import nullcontext
from django.core.management.base import BaseCommand, CommandError
from assistant.model_benchmark import BenchmarkError, failure, run_benchmark
import time


class Command(BaseCommand):
    help = 'Compare three installed models sequentially; requires inactive application/voice services. Emits JSONL.'

    def add_arguments(self, parser):
        parser.add_argument('--repeats', type=int, default=1)
        parser.add_argument('--quick', action='store_true', help='18 requests covering Pi specifications, IoT and concise voice; full mode is 84.')
        parser.add_argument('--output', help='New JSONL file; refuses to overwrite an existing report. Flushes and fsyncs every event.')

    def handle(self, *args, **options):
        started = time.monotonic()
        def emit(row):
            line = json.dumps(row)
            if report is not None:
                report.write(line+'\n')
                report.flush()
                os.fsync(report.fileno())
            self.stdout.write(line)
            self.stdout.flush()
        try:
            with (open(options['output'], 'x', encoding='utf-8') if options['output'] else nullcontext(None)) as report:
                run_benchmark(emit, options['repeats'], quick=options['quick'])
        except BenchmarkError as exc:
            raise CommandError(str(exc)) from None
        except KeyboardInterrupt:
            raise CommandError(json.dumps({'type':'error','operation':'benchmark','category':'interrupted',
                'model':None,'elapsed_ms':round((time.monotonic()-started)*1000)}),returncode=130) from None
        except Exception as exc:
            # Do not expose arbitrary HTTP exception bodies or configured URLs.
            raise CommandError(str(failure(exc,'benchmark',None,started))) from None

import json
from django.core.management.base import BaseCommand, CommandError
from knowledge_base.library import initialize_library


class Command(BaseCommand):
    help = 'Initialize/resume hash-pinned, licensed bundled reference documents.'

    def handle(self, *args, **options):
        result = initialize_library()
        self.stdout.write(json.dumps(result))
        if result['failed'] or not result['indexed'] + result['unchanged']:
            raise CommandError('Reference initialization incomplete; inspect source errors.')

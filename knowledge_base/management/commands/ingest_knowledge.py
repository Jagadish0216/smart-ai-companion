from django.core.management.base import BaseCommand, CommandError

from knowledge_base.services import (
    KnowledgeIngestionError,
    KnowledgeIngestionService,
)


class Command(BaseCommand):
    help = "Ingest or replace a local UTF-8 text/Markdown knowledge source."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to a .txt, .md, or .markdown file")
        parser.add_argument(
            "--source-id",
            dest="source_identifier",
            help="Stable source identifier; defaults to the resolved file path",
        )

    def handle(self, *args, **options):
        try:
            result = KnowledgeIngestionService().ingest_path(
                options["path"],
                source_identifier=options.get("source_identifier"),
            )
        except KnowledgeIngestionError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(
            f"Indexed {result.chunk_count} chunk(s) from "
            f"{result.document.filename} (document {result.document.pk})."
        ))

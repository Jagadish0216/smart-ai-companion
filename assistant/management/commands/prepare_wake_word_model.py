"""Explicit installation of wake-model assets; never run during Django import."""

from dataclasses import replace

from django.core.management.base import BaseCommand, CommandError

from assistant.voice.wakeword.base import WakeWordConfig
from assistant.voice.wakeword.models import prepare_model


class Command(BaseCommand):
    help = "Download the selected built-in wake model and shared assets (or shared assets for a custom model)."

    def add_arguments(self, parser):
        parser.add_argument("--model", help="Built-in model name; overrides configured MODEL/MODEL_PATH for this command only.")

    def handle(self, *args, **options):
        try:
            config = WakeWordConfig.from_settings()
            if options.get("model"):
                config = replace(config, model=options["model"], model_path="")
                config.validate()
            self.stdout.write(f"Preparing wake word: engine={config.engine} model={config.selected_model}")
            prepare_model(config)
        except Exception as exc:
            raise CommandError(f"Wake model preparation failed: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"Wake model ready: {config.selected_model}"))

"""Explicit installation of wake-model assets; never run during Django import."""

from dataclasses import replace

from django.core.management.base import BaseCommand, CommandError

from assistant.voice.wakeword.base import WakeWordConfig
from assistant.voice.wakeword.models import prepare_model, load_engine, validate_installed_assets


class Command(BaseCommand):
    help = "Download the selected built-in wake model and shared assets (or shared assets for a custom model)."

    def add_arguments(self, parser):
        parser.add_argument("--check-only", action="store_true", help="Check existing assets offline; never download or modify files.")
        parser.add_argument("--model", help="Built-in model name; overrides configured MODEL/MODEL_PATH for this command only.")

    def handle(self, *args, **options):
        try:
            config = WakeWordConfig.from_settings()
            if options.get("model"):
                config = replace(config, model=options["model"], model_path="")
                config.validate()
            self.stdout.write(f"Preparing wake word: engine={config.engine} model={config.selected_model}")
            if options.get("check_only"):
                validate_installed_assets(config, load_engine())
            else:
                prepare_model(config)
        except Exception as exc:
            raise CommandError(f"Wake model preparation failed: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"Wake model ready: {config.selected_model}"))

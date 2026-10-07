"""Send one display state through the same bridge as the voice loop."""

from django.core.management.base import BaseCommand, CommandError

from assistant.display import DisplayState, get_companion_display


class Command(BaseCommand):
    help = "Send a state to the configured ESP32 companion display (no acknowledgment wait)."

    def add_arguments(self, parser):
        parser.add_argument("state", choices=[state.value for state in DisplayState])

    def handle(self, *args, **options):
        try:
            state = DisplayState(options["state"])
        except ValueError as exc:
            raise CommandError("Unknown companion display state.") from exc
        display = get_companion_display()
        try:
            if not display.enabled:
                raise CommandError("Companion display support is " + display.reason + ".")
            if not display.set_state(state):
                raise CommandError("Companion display state could not be sent; check configuration, port and permissions.")
            self.stdout.write(self.style.SUCCESS(f"Sent {state.value} to companion display (not acknowledgment-verified)."))
        finally:
            display.close()

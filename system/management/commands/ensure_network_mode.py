from django.core.management.base import BaseCommand, CommandError

from system.control_plane.provisioning import ensure_network_mode


class Command(BaseCommand):
    help = "Choose bounded Wi-Fi client or first-boot setup AP mode."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force-setup",
            action="store_true",
            help="Explicitly enter recovery/setup AP mode.",
        )
        parser.add_argument(
            "--prefer-client",
            action="store_true",
            help=(
                "Ignore a persisted setup request once and try active/saved "
                "client Wi-Fi before restoring the AP."
            ),
        )

    def handle(self, *args, **options):
        if options["force_setup"] and options["prefer_client"]:
            raise CommandError("Choose either --force-setup or --prefer-client, not both.")
        result = ensure_network_mode(
            force_setup=options["force_setup"],
            prefer_client=options["prefer_client"],
        )
        summary = f"{result['state']}: {result['message']}"
        if not result["success"]:
            raise CommandError(summary)
        self.stdout.write(self.style.SUCCESS(summary))

from getpass import getpass
from django.core.management.base import BaseCommand, CommandError
from system.control_plane.demo_ap import configure


class Command(BaseCommand):
    help = 'Configure the protected demo profile without activating it; password is prompted, never logged.'

    def handle(self, *args, **options):
        password = getpass('New demo hotspot WPA2 password (16+ characters recommended): ')
        if password != getpass('Confirm password: '):
            raise CommandError('Passwords do not match.')
        try:
            gateway = configure(password)
        except (ValueError, RuntimeError, IndexError) as exc:
            raise CommandError(str(exc)) from None
        finally:
            del password
        self.stdout.write(f'Protected profile prepared, not activated. Configured gateway: {gateway}')

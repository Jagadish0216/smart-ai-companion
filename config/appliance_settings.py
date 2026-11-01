"""Opt-in trusted-LAN settings. Does not change the default appliance startup."""
import os

from django.core.exceptions import ImproperlyConfigured

from .settings import *  # noqa: F403

if (len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5  # noqa: F405
        or SECRET_KEY.startswith('django-insecure-')  # noqa: F405
        or SECRET_KEY == 'your-secret-key-here'):  # noqa: F405
    raise ImproperlyConfigured('The appliance profile requires a unique SECRET_KEY of at least 50 characters.')

ALLOWED_HOSTS = [host.strip() for host in ALLOWED_HOSTS if host.strip()]  # noqa: F405
if not ALLOWED_HOSTS or '*' in ALLOWED_HOSTS:
    raise ImproperlyConfigured('The appliance profile requires explicit ALLOWED_HOSTS without a wildcard.')

DEBUG = False
# LAN/hotspot HTTP remains usable. HTTPS is a deliberate opt-in, not an inference
# from a proxy header supplied by an arbitrary client.
COMPANION_HTTPS = os.environ.get('COMPANION_HTTPS', 'false').lower() in {'1', 'true', 'yes', 'on'}
SECURE_SSL_REDIRECT = COMPANION_HTTPS
SESSION_COOKIE_SECURE = COMPANION_HTTPS
CSRF_COOKIE_SECURE = COMPANION_HTTPS
SECURE_HSTS_SECONDS = int(os.environ.get('COMPANION_HSTS_SECONDS', '0')) if COMPANION_HTTPS else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False

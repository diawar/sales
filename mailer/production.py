"""HTTPS server profile for the no-send acceptance stage."""
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured


def required(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise ImproperlyConfigured(f'{name} is required for production')
    return value


# Validate before importing settings: do not create a directory from invalid input.
_secret = required('DJANGO_SECRET_KEY')
if len(_secret) < 50 or len(set(_secret)) < 5 or _secret.startswith('django-insecure-'):
    raise ImproperlyConfigured('DJANGO_SECRET_KEY must be a permanent random secret of at least 50 characters')
_root = Path(__file__).resolve().parent.parent
_data = Path(required('SALES_DATA_DIR')).expanduser()
if not _data.is_absolute() or _data.resolve().is_relative_to(_root) or _root.is_relative_to(_data.resolve()):
    raise ImproperlyConfigured('SALES_DATA_DIR must be an absolute directory outside the code tree, not its parent')
_hosts = [h.strip().lower() for h in required('SALES_ALLOWED_HOSTS').split(',')]
if any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', h) or '..' in h for h in _hosts):
    raise ImproperlyConfigured('SALES_ALLOWED_HOSTS must contain exact hostnames without wildcards, ports or URLs')
_origin = required('SALES_PUBLIC_BASE_URL')
try:
    _url = urlsplit(_origin)
    _valid_origin = (_url.scheme == 'https' and _url.hostname in _hosts and
                     _url.port in (None, 443) and not _url.username and not _url.password and
                     _url.path in ('', '/') and not _url.query and not _url.fragment and
                     not any(c.isspace() for c in _origin))
except ValueError:
    _valid_origin = False
if not _valid_origin:
    raise ImproperlyConfigured('SALES_PUBLIC_BASE_URL must be an HTTPS origin in SALES_ALLOWED_HOSTS')

from .settings import *  # noqa: E402,F403

SECRET_KEY = _secret
DATA_DIR = _data.resolve()
ALLOWED_HOSTS = _hosts
# Rebuild SQLite path even when base settings were previously imported by tooling.
if not os.environ.get('POSTGRES_DB'):
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3',
                            'NAME': DATA_DIR / 'contacts.sqlite3',
                            'OPTIONS': {'timeout': 20, 'transaction_mode': 'IMMEDIATE'}}}
DEBUG = False
SALES_AUTH_REQUIRED = True
SALES_PUBLIC_BASE_URL = _origin.rstrip('/')
CSRF_TRUSTED_ORIGINS = [SALES_PUBLIC_BASE_URL]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
# Only safe behind the loopback proxy that overwrites this header (deploy/nginx).
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = False
SECURE_HSTS_SECONDS = 0  # Enable after verifying TLS, see deployment instructions.
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
MIDDLEWARE = [*MIDDLEWARE, 'django.middleware.clickjacking.XFrameOptionsMiddleware']
X_FRAME_OPTIONS = 'DENY'

# Deliberate hard lock for this stage, even if an old environment enables SMTP.
SALES_SMTP_ENABLED = False
EMAIL_BACKEND = 'django.core.mail.backends.dummy.EmailBackend'

"""Production entry point. Use manage.py for local development."""
import os

from django.core.wsgi import get_wsgi_application

# This entry point deliberately cannot fall back to a legacy SMTP-enabled profile.
os.environ['DJANGO_SETTINGS_MODULE'] = 'mailer.production'
application = get_wsgi_application()

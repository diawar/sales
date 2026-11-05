import os
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True)
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'local-development-only-do-not-deploy')
DEBUG = False
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'testserver']
INSTALLED_APPS = ['django.contrib.contenttypes', 'contacts']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware']
ROOT_URLCONF = 'mailer.urls'
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': DATA_DIR / 'contacts.sqlite3', 'OPTIONS': {'timeout': 20, 'transaction_mode': 'IMMEDIATE'}}}
if os.environ.get('POSTGRES_DB'):
    DATABASES['default'] = {'ENGINE': 'django.db.backends.postgresql', 'NAME': os.environ['POSTGRES_DB'], 'USER': os.environ['POSTGRES_USER'], 'PASSWORD': os.environ['POSTGRES_PASSWORD'], 'HOST': os.environ.get('POSTGRES_HOST', 'localhost'), 'PORT': os.environ.get('POSTGRES_PORT', '5432')}
USE_TZ = True
TIME_ZONE = 'Asia/Qyzylorda'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
DATA_UPLOAD_MAX_MEMORY_SIZE = 3 * 1024 * 1024
CSRF_COOKIE_SAMESITE = 'Strict'
SESSION_COOKIE_SAMESITE = 'Strict'
SECURE_CONTENT_TYPE_NOSNIFF = True

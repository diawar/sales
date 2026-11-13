import os
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get('SALES_DATA_DIR', BASE_DIR / 'data')).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'local-development-only-do-not-deploy')
DEBUG = False
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'testserver']
INSTALLED_APPS = ['django.contrib.contenttypes','django.contrib.auth','django.contrib.sessions','contacts']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware','django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware',
    'contacts.action_audit.ActionAuditMiddleware','contacts.accounts.AccessMiddleware','django.middleware.csrf.CsrfViewMiddleware']
TEMPLATES = [{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[BASE_DIR/'contacts'/'templates'],
    'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.request','django.contrib.auth.context_processors.auth']}}]
LANGUAGE_CODE='ru'
LOGIN_URL='/login/'
SALES_AUTH_REQUIRED=True
SESSION_COOKIE_HTTPONLY=True
SESSION_COOKIE_NAME='sales_sessionid'
CSRF_COOKIE_NAME='sales_csrftoken'
CSRF_FAILURE_VIEW='contacts.accounts.csrf_failure'
SESSION_COOKIE_AGE=28800
SESSION_EXPIRE_AT_BROWSER_CLOSE=True
SESSION_COOKIE_SECURE=os.environ.get('SALES_SECURE_COOKIES','0')=='1'
CSRF_COOKIE_SECURE=SESSION_COOKIE_SECURE
AUTH_PASSWORD_VALIDATORS=[{'NAME':'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator','OPTIONS':{'min_length':12}},
    {'NAME':'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME':'django.contrib.auth.password_validation.NumericPasswordValidator'}]
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
SALES_AI_KEY = os.environ.get('OPENAI_API_KEY','')
SALES_AI_MODEL = os.environ.get('SALES_AI_MODEL','')
SALES_SMTP_ENABLED = os.environ.get('SALES_SMTP_ENABLED','0') == '1'
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.environ.get('EMAIL_HOST','')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT','587'))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER','')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD','')
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS','1') == '1'
EMAIL_USE_SSL = os.environ.get('EMAIL_USE_SSL','0') == '1'
EMAIL_TIMEOUT = 20
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL','')
SALES_FROM_NAME = os.environ.get('SALES_FROM_NAME','OVER')
SALES_TEST_RECIPIENTS = [v.strip() for v in os.environ.get('SALES_TEST_RECIPIENTS','').split(',') if v.strip()]

SALES_PUBLIC_BASE_URL = os.environ.get('SALES_PUBLIC_BASE_URL','')

# Review stage: Servercore can be inspected, but cannot send mail.
SALES_MAIL_PROVIDER = os.environ.get('SALES_MAIL_PROVIDER', 'smtp')
SERVERCORE_RESOURCE_ID = os.environ.get('SERVERCORE_RESOURCE_ID', '')
SERVERCORE_IAM_TOKEN = os.environ.get('SERVERCORE_IAM_TOKEN', '')
SERVERCORE_ACCOUNT_ID = os.environ.get('SERVERCORE_ACCOUNT_ID', '')
SERVERCORE_PROJECT_NAME = os.environ.get('SERVERCORE_PROJECT_NAME', '')
SERVERCORE_SERVICE_USER = os.environ.get('SERVERCORE_SERVICE_USER', '')
SERVERCORE_SERVICE_PASSWORD = os.environ.get('SERVERCORE_SERVICE_PASSWORD', '')
SERVERCORE_SENDER_DOMAIN = os.environ.get('SERVERCORE_SENDER_DOMAIN', '')
if SALES_MAIL_PROVIDER == 'servercore' or EMAIL_HOST.casefold() == 'smtp.servercore-smtp.com':
    EMAIL_BACKEND = 'contacts.servercore.ReviewOnlyBackend'

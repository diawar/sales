"""Servercore connection adapter. Read-only API; no SMTP sending in this stage."""
import threading
import uuid
from datetime import datetime, timedelta
import httpx
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.core.mail.backends.base import BaseEmailBackend
from django.utils import timezone

API_BASE = 'https://api.servercore.com/ses'
IDENTITY = 'https://cloud.api.servercore.com/identity/v3/auth/tokens'
SMTP_HOST = 'smtp.servercore-smtp.com'
_lock = threading.Lock()
_cached = None


class ReviewOnlyBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        raise RuntimeError('Servercore: реальная отправка отключена на этапе проверки.')


def configuration():
    missing = []
    try: uuid.UUID(settings.SERVERCORE_RESOURCE_ID)
    except (ValueError, TypeError, AttributeError): missing.append('SERVERCORE_RESOURCE_ID')
    credentials = bool(settings.SERVERCORE_IAM_TOKEN) or all((settings.SERVERCORE_ACCOUNT_ID,
        settings.SERVERCORE_PROJECT_NAME, settings.SERVERCORE_SERVICE_USER, settings.SERVERCORE_SERVICE_PASSWORD))
    if not credentials: missing.append('SERVERCORE_IAM_TOKEN или реквизиты сервисного пользователя')
    smtp_missing = []
    if settings.EMAIL_HOST != SMTP_HOST: smtp_missing.append('EMAIL_HOST=smtp.servercore-smtp.com')
    if not ((settings.EMAIL_PORT == 1126 and settings.EMAIL_USE_TLS and not settings.EMAIL_USE_SSL) or
            (settings.EMAIL_PORT == 1127 and settings.EMAIL_USE_SSL and not settings.EMAIL_USE_TLS)):
        smtp_missing.append('1126 STARTTLS или 1127 TLS')
    if not settings.EMAIL_HOST_USER or not settings.EMAIL_HOST_PASSWORD: smtp_missing.append('SMTP-логин и пароль')
    try: validate_email(settings.DEFAULT_FROM_EMAIL)
    except ValidationError: smtp_missing.append('DEFAULT_FROM_EMAIL')
    return {'provider':'servercore', 'can_send':False, 'mode':'review', 'api_configured':not missing,
        'api_missing':missing, 'smtp_configured':not smtp_missing, 'smtp_missing':smtp_missing,
        'smtp_host':SMTP_HOST, 'smtp_port':settings.EMAIL_PORT,
        'token_mode':'temporary' if settings.SERVERCORE_IAM_TOKEN else 'service_user' if credentials else 'missing',
        'domain':settings.SERVERCORE_SENDER_DOMAIN, 'limit':{'messages':20,'seconds':300},
        'note':'Проверка API не отправляет письма. SMTP-отправка заблокирована в коде. Лимит повышается через поддержку.'}


def _request(method, url, **kwargs):
    # Fixed provider URLs, TLS verification and no redirect forwarding of credentials.
    try:
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
            response = client.request(method, url, **kwargs)
        if response.status_code in (401,403): raise ValueError('Нет доступа Servercore: проверьте проект, права и срок IAM-токена.')
        if response.status_code == 429: raise ValueError('Лимит API Servercore. Повторите проверку позже.')
        if not 200 <= response.status_code < 300: raise ValueError('Servercore вернул ошибку. Проверьте ресурс и повторите позже.')
        return response
    except httpx.HTTPError:
        raise ValueError('Не удалось связаться с Servercore. Проверьте соединение и повторите позже.') from None


def iam_token():
    global _cached
    if settings.SERVERCORE_IAM_TOKEN: return settings.SERVERCORE_IAM_TOKEN
    key = (settings.SERVERCORE_ACCOUNT_ID, settings.SERVERCORE_PROJECT_NAME,
           settings.SERVERCORE_SERVICE_USER, settings.SERVERCORE_SERVICE_PASSWORD)
    if not all(key): raise ValueError('Не настроены реквизиты API Servercore.')
    with _lock:
        if _cached and _cached[0] == key and _cached[2] > timezone.now(): return _cached[1]
        response = _request('POST', IDENTITY, json={'auth':{'identity':{'methods':['password'],
            'password':{'user':{'name':key[2],'domain':{'name':key[0]},'password':key[3]}}},
            'scope':{'project':{'name':key[1],'domain':{'name':key[0]}}}}})
        try:
            token = response.headers['X-Subject-Token']
            expiry = datetime.fromisoformat(response.json()['token']['expires_at'].replace('Z','+00:00'))
            if not token or expiry.tzinfo is None or expiry <= timezone.now()+timedelta(minutes=1): raise ValueError()
        except (KeyError,ValueError,TypeError,AttributeError):
            raise ValueError('Некорректный ответ авторизации Servercore.') from None
        _cached = (key, token, expiry-timedelta(minutes=1))
        return token


def read_resource(suffix='', params=None):
    if not configuration()['api_configured']: raise ValueError('Сначала заполните параметры API Servercore в окружении сервера.')
    resource = str(uuid.UUID(settings.SERVERCORE_RESOURCE_ID))
    response = _request('GET', f'{API_BASE}/resources/{resource}{suffix}',
                        params=params, headers={'X-Auth-Token':iam_token()})
    try:
        data = response.json()
        if not isinstance(data,dict): raise ValueError()
        return data
    except (ValueError,TypeError): raise ValueError('Некорректный ответ API Servercore.') from None


def inspect_connection():
    value = read_resource().get('resource')
    if not isinstance(value,dict) or not value.get('id'): raise ValueError('В ответе Servercore отсутствует ресурс.')
    # The resource response contains a password. Never return or persist raw responses.
    result = {'resource':{'id':str(value['id']), 'name':str(value.get('name',''))[:160],
                         'status':str(value.get('status',''))[:30]}, 'can_send':False, 'sent':False}
    domain = settings.SERVERCORE_SENDER_DOMAIN
    if domain:
        try: validate_email('sender@'+domain)
        except ValidationError: raise ValueError('Проверьте SERVERCORE_SENDER_DOMAIN.')
        verification = read_resource('/domains/check', {'name':domain}).get('verification',{})
        if not isinstance(verification,dict): raise ValueError('В ответе Servercore отсутствуют статусы домена.')
        result['verification'] = {key:verification.get(key) is True for key in
            ('dkim_status','dmarc_status','dns_status','spf_status','verification_status')}
    return result


def delivery_status(message_id='', x_track_id=''):
    if not message_id and not x_track_id: raise ValueError('Нужен идентификатор письма.')
    return read_resource('/issue/status', {k:v for k,v in {'message_id':message_id,'x_track_id':x_track_id}.items() if v})

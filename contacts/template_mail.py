"""Single-recipient test sends only; no audience sending or delivery claims."""
import hashlib
import json
import smtplib
import uuid
from datetime import timedelta
from email.utils import formataddr
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import TemplateTestMail,TemplateTestMailGate
from .template_tools import check_template,personalize
from .views import error


def configuration():
    missing=[]
    if settings.SALES_MAIL_PROVIDER == 'servercore' or settings.EMAIL_HOST.casefold() == 'smtp.servercore-smtp.com':
        missing.append('Servercore: реальная отправка заблокирована на этапе проверки')
    if not settings.SALES_SMTP_ENABLED:missing.append('SALES_SMTP_ENABLED=1')
    if not settings.EMAIL_HOST or any(c in settings.EMAIL_HOST for c in '/\r\n '):missing.append('EMAIL_HOST')
    if not isinstance(settings.EMAIL_PORT,int) or not 1<=settings.EMAIL_PORT<=65535:missing.append('EMAIL_PORT')
    if bool(settings.EMAIL_USE_TLS)==bool(settings.EMAIL_USE_SSL):missing.append('один режим: TLS или SSL')
    if settings.EMAIL_HOST_USER and not settings.EMAIL_HOST_PASSWORD:missing.append('EMAIL_HOST_PASSWORD')
    try:validate_email(settings.DEFAULT_FROM_EMAIL)
    except ValidationError:missing.append('DEFAULT_FROM_EMAIL')
    if any(c in settings.SALES_FROM_NAME for c in '\r\n'):missing.append('SALES_FROM_NAME')
    recipients=[]
    for recipient in settings.SALES_TEST_RECIPIENTS:
        try:validate_email(recipient)
        except ValidationError:missing.append('корректный SALES_TEST_RECIPIENTS');continue
        recipients.append(recipient)
    if not recipients:missing.append('SALES_TEST_RECIPIENTS')
    return {'configured':not missing,'missing':missing,'sender':settings.DEFAULT_FROM_EMAIL,'recipients':recipients,
            'note':'SMTP принимает письмо на отправку; это не подтверждение доставки или прочтения. В тесте отписка отключена.'}


def report(row):
    return {'token':str(row.token),'recipient':row.recipient,'subject':row.subject,'state':row.state,'detail':row.detail,'created_at':row.created_at,'message_id':row.message_id}


def build_message(data,token):
    values=data.get('values',{})
    html,_=personalize(data['html'],values,True)
    subject,_=personalize(data['subject'],values)
    preheader,_=personalize(data.get('preheader',''),values)
    if any(c in subject for c in '\r\n') or len(subject)>250:raise ValueError('После подстановки тема содержит перенос строки или длиннее 250 символов.')
    soup=BeautifulSoup(html,'html.parser')
    for anchor in soup.find_all('a',href='https://example.com/unsubscribe-preview'):
        anchor.attrs.pop('href',None);anchor.string='Отписка — в тесте отключена'
    banner=soup.new_tag('p');banner.string='ТЕСТОВОЕ ПИСЬМО. Отправлено только на указанный тестовый адрес; отписка не меняет подписки.'
    body=soup.body if soup.body else soup
    body.insert(0,banner)
    if preheader:
        header=soup.new_tag('div');header['style']='display:none;max-height:0;overflow:hidden';header.string=preheader;body.insert(0,header)
    source=str(soup)
    text_soup=BeautifulSoup(source,'html.parser')
    for tag in text_soup.find_all(['style','script','head']):tag.decompose()
    domain=settings.DEFAULT_FROM_EMAIL.rsplit('@',1)[-1]
    message_id=f'<sales-test-{token}@{domain}>'
    message=EmailMultiAlternatives('[ТЕСТ] '+subject,text_soup.get_text('\n',strip=True),
        formataddr((settings.SALES_FROM_NAME,settings.DEFAULT_FROM_EMAIL)),[data['recipient']],headers={'Message-ID':message_id})
    message.attach_alternative(source,'text/html')
    return message,message_id


@require_http_methods(['GET','POST'])
def test_mail(request):
    if request.method=='GET':
        payload=configuration();payload['history']=[report(row) for row in TemplateTestMail.objects.order_by('-created_at')[:10]]
        return JsonResponse(payload)
    try:
        data=json.loads(request.body)
        if not isinstance(data,dict):raise ValueError()
        token=uuid.UUID(data.get('token',''))
        for key,limit in [('recipient',254),('subject',250),('preheader',250),('html',500000)]:
            if not isinstance(data.get(key,''),str) or len(data.get(key,''))>limit:raise ValueError()
        validate_email(data.get('recipient',''))
        values=data.get('values',{})
        if not isinstance(values,dict) or any(k not in {'name','company','city','email'} or not isinstance(v,str) or len(v)>250 for k,v in values.items()):raise ValueError()
        data={k:data.get(k,'') for k in ('recipient','subject','preheader','html')}|{'values':values}
        fingerprint=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    except (ValueError,TypeError,AttributeError,ValidationError):return error('Нужны корректный тестовый email, тема, HTML и идентификатор отправки.')
    existing=TemplateTestMail.objects.filter(pk=token).first()
    if existing:
        if existing.fingerprint!=fingerprint:return error('Этот идентификатор уже использован для другого письма.',409)
        return JsonResponse(report(existing))
    from .models import Address
    from .email_policy import sending_state
    address=Address.objects.filter(email__iexact=data['recipient']).first()
    if address and sending_state(address)['code'] != 'ready':
        return error('Адрес не допущен к отправке: '+sending_state(address)['reason'], 409)
    config=configuration()
    if not config['configured']:return error('SMTP не настроен. Заполните параметры окружения сервера.',503)
    if data['recipient'].casefold() not in {v.casefold() for v in config['recipients']}:return error('Адрес не включён в список тестовых получателей на сервере.',403)
    checks=check_template(data['subject'],data['preheader'],data['html'],values)
    if checks['errors']:return JsonResponse({'error':'Исправьте ошибки шаблона перед тестовой отправкой.','checks':checks},status=400)
    try:message,message_id=build_message(data,token)
    except ValueError as exc:return error(str(exc))
    now=timezone.now()
    with transaction.atomic():
        gate,_=TemplateTestMailGate.objects.get_or_create(pk=1)
        gate=TemplateTestMailGate.objects.select_for_update().get(pk=gate.pk)
        # Recheck after the global lock: concurrent retries must never send twice.
        existing=TemplateTestMail.objects.filter(pk=token).first()
        if existing:
            if existing.fingerprint!=fingerprint:return error('Идентификатор уже использован.',409)
            return JsonResponse(report(existing))
        if (gate.next_at and gate.next_at>now) or TemplateTestMail.objects.filter(created_at__gte=now-timedelta(hours=24)).count()>=30:return error('Лимит тестов: пауза 10 секунд и не более 30 попыток за 24 часа.',429)
        gate.next_at=now+timedelta(seconds=10);gate.save()
        row=TemplateTestMail.objects.create(token=token,fingerprint=fingerprint,recipient=data['recipient'],subject=message.subject,message_id=message_id)
    try:
        accepted=message.send(fail_silently=False)
        row.state='accepted' if accepted==1 else 'failed'
        row.detail='Принято SMTP-сервером. Доставка в ящик не подтверждена.' if accepted==1 else 'Сервер не принял письмо.'
    except smtplib.SMTPAuthenticationError:row.state='failed';row.detail='Ошибка авторизации SMTP. Проверьте настройки на сервере.'
    except (smtplib.SMTPRecipientsRefused,smtplib.SMTPSenderRefused,smtplib.SMTPDataError):row.state='failed';row.detail='SMTP отклонил получателя, отправителя или содержимое письма.'
    except (OSError,smtplib.SMTPException):row.state='unknown';row.detail='Соединение прервано. Результат неизвестен; автоматического повтора нет. Проверьте почту и журнал провайдера.'
    except Exception:row.state='unknown';row.detail='Не удалось подтвердить результат. Проверьте почту и журнал провайдера перед новым тестом.'
    row.save(update_fields=['state','detail','updated_at'])
    return JsonResponse(report(row))

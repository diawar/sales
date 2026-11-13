"""Opaque per-message links, first observations and isolated no-send previews."""
import hashlib
import secrets
from email.utils import formataddr
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from .models import EmailSubscription, TrackedMessage, TrackingLink, TrackingEvent
from .subscriptions import issue, CONSENT_TEXT, CONSENT_VERSION, SCOPE
from .template_tools import personalize, check_template


def digest(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def public_origin(request):
    base = settings.SALES_PUBLIC_BASE_URL.rstrip('/')
    if not base:
        # Never turn an arbitrary request Host into a link in a future mailing.
        if request.get_host().split(':')[0] not in ('localhost','127.0.0.1','testserver'):
            raise ValueError('Укажите SALES_PUBLIC_BASE_URL с публичным HTTPS-адресом приложения.')
        return request.build_absolute_uri('/').rstrip('/')
    try:
        URLValidator(schemes=['https','http'])(base)
        p = urlsplit(base)
        if p.username or p.password or p.query or p.fragment or p.path not in ('','/'):
            raise ValidationError('origin')
        if p.scheme != 'https' and p.hostname not in ('localhost','127.0.0.1'): raise ValidationError('https')
    except (ValueError,ValidationError):
        raise ValueError('SALES_PUBLIC_BASE_URL: нужен HTTPS-адрес без пути, параметров и пароля (HTTP допустим локально).') from None
    return base


def safe_target(target):
    try:
        if not isinstance(target,str) or len(target)>2000 or any(ord(c)<32 for c in target) or '\\' in target:
            raise ValidationError('url')
        URLValidator(schemes=['http','https'])(target)
        p=urlsplit(target)
        if p.username or p.password: raise ValidationError('credentials')
    except (ValidationError,ValueError): raise ValueError('В письме есть некорректная ссылка перехода.') from None
    return target


def new_link(message, kind, origin, target=''):
    raw=secrets.token_urlsafe(32)
    TrackingLink.objects.create(message=message,kind=kind,target=target,token_hash=digest(raw))
    return origin+reverse('tracking-open' if kind=='open' else 'tracking-click',args=[raw])


@transaction.atomic
def prepare_preview(request, template, recipient, values, campaign=None, template_snapshot=None, sender_snapshot=None, metadata=None, subscription_scope=SCOPE):
    """Always test-only: no SMTP, no real address/subscription writes, no sent event."""
    if not isinstance(recipient,str) or len(recipient)>254: raise ValueError('Проверьте email для проверки.')
    recipient=recipient.strip().casefold()
    try: validate_email(recipient)
    except ValidationError: raise ValueError('Проверьте email для проверки.')
    if not isinstance(values,dict) or any(k not in {'name','company','city'} or not isinstance(v,str) or len(v)>250 for k,v in values.items()):
        raise ValueError('Проверьте значения персонализации.')
    context={**values,'email':recipient}
    saved=template_snapshot if template_snapshot is not None else {k:getattr(template,k) for k in ('id','name','revision','subject','preheader','html')}
    diagnosis=check_template(saved['subject'],saved['preheader'],saved['html'],context)
    if diagnosis['errors']: raise ValueError('Исправьте ошибки шаблона: '+ '; '.join(f['message'] for f in diagnosis['findings'] if f['level']=='error'))
    subject,subject_issues=personalize(saved['subject'],context)
    source,html_issues=personalize(saved['html'],context,True)
    preheader,preheader_issues=personalize(saved['preheader'],context)
    if subject_issues or html_issues or preheader_issues: raise ValueError('Заполните персонализацию или запасные значения переменных.')
    if not subject.strip() or len(subject)>250 or any(c in subject for c in '\r\n'):
        raise ValueError('После персонализации тема должна быть не длиннее 250 символов и без переносов строк.')
    origin=public_origin(request)
    subscription,_=EmailSubscription.objects.get_or_create(email=recipient,scope=subscription_scope,is_test=True,
        defaults={'source':'Проверка ссылок письма (без отправки)','consent_text':CONSENT_TEXT,'consent_version':CONSENT_VERSION})
    # Issuing an unsubscribe link is not consent, confirmation or ownership proof.
    raw,token=issue(subscription,'unsubscribe')
    unsubscribe_url=origin+reverse('subscription-public',args=[raw])
    message=TrackedMessage.objects.create(template=template,campaign=campaign,recipient=recipient,
        subject=subject,is_test=True,unsubscribe_token=token,expires_at=token.expires_at,
        preparation_snapshot={'template':{k:saved.get(k) for k in ('id','name','revision')},'sender':sender_snapshot or {},**(metadata or {})})
    soup=BeautifulSoup(source,'html.parser')
    urls={};clicks=[]
    for anchor in soup.find_all('a',href=True):
        target=str(anchor['href']).strip()
        if target=='https://example.com/unsubscribe-preview': anchor['href']=unsubscribe_url;continue
        if target.startswith(('mailto:','tel:','#')) or not target: continue
        safe_target(target)
        if urlsplit(target).netloc==urlsplit(origin).netloc and urlsplit(target).path.startswith(('/track/','/subscription/')):
            raise ValueError('Используйте исходную ссылку магазина, а для отписки — {{unsubscribe_url}}.')
        if target not in urls:
            urls[target]=new_link(message,'click',origin,target)
            clicks.append({'target':target,'url':urls[target]})
        anchor['href']=urls[target]
    pixel=new_link(message,'open',origin)
    image=soup.new_tag('img',src=pixel,alt='',width='1',height='1')
    image['style']='width:1px;height:1px;border:0;display:block'
    container=soup.body if soup.body else soup
    if preheader:
        header=soup.new_tag('div');header['style']='display:none;max-height:0;overflow:hidden';header.string=preheader;container.insert(0,header)
    container.append(image)
    headers={'Message-ID':f'<sales-{message.pk}@{urlsplit(origin).hostname}>',
             'X-Track-ID':str(message.pk),'List-Unsubscribe':f'<{unsubscribe_url}>'}
    if sender_snapshot:
        headers['From']=formataddr((sender_snapshot['name'],sender_snapshot['email']))
        if sender_snapshot.get('reply_to'):headers['Reply-To']=sender_snapshot['reply_to']
    return {'id':str(message.pk),'is_test':True,'sent':False,'subject':subject,'html':str(soup),
        'campaign_id':campaign.pk if campaign else None,'preparation_snapshot':message.preparation_snapshot,
        'pixel_url':pixel,'clicks':clicks,'unsubscribe_url':unsubscribe_url,
        'headers':headers,
        'note':'Письмо подготовлено только для проверки. Отправки и подтверждения доставки нет.'}


def resolve_link(raw,kind):
    if not isinstance(raw,str) or not 30<=len(raw)<=100: return None
    link=TrackingLink.objects.select_related('message').filter(token_hash=digest(raw),kind=kind).first()
    if not link or link.message.expires_at<=timezone.now(): return None
    if not link.message.is_test and link.message.state!='accepted': return None
    return link


def observe(link):
    event,_=TrackingEvent.objects.get_or_create(message=link.message,link=link,kind=link.kind)
    from .rehearsal import capture
    capture(event)


def observe_unsubscribe(token):
    message=TrackedMessage.objects.filter(unsubscribe_token=token).first()
    if message:
        # All subscription mutations lock the subscription first, so concurrent POSTs serialize.
        event,_=TrackingEvent.objects.get_or_create(message=message,kind='unsubscribe',link=None)
        from .rehearsal import capture
        capture(event)

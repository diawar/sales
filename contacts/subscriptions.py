"""Consent records and bearer links. No email is sent by this module."""
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlsplit
from django.db import transaction
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.core.validators import validate_email,URLValidator
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import EmailSubscription,SubscriptionEvent,SubscriptionToken,Address,EmailExclusion
from .email_policy import REASONS

SCOPE='over_email_offers'
CONSENT_VERSION='over-email-offers-v1'
CONSENT_TEXT='Я согласен получать email-рассылку OVER с акциями и предложениями на указанный адрес. Я могу в любое время отказаться от рассылки по ссылке отписки.'


def canonical(email):return email.strip().casefold()

def revoked_emails():
    return EmailSubscription.objects.filter(is_test=False).filter(Q(state='revoked')|Q(revoked_at__isnull=False)).values_list('email',flat=True)

def has_revocation(email):
    return revoked_emails().filter(email=canonical(email)).exists()


def audit(row,kind,source):
    SubscriptionEvent.objects.create(subscription=row,kind=kind,source=source,snapshot={
        'email':row.email,'state':row.state,'is_test':row.is_test,'scope':row.scope,
        'source':row.source,'source_url':row.source_url,'source_at':row.source_at.isoformat(),
        'evidence':row.evidence,'consent_text':row.consent_text,'consent_version':row.consent_version,
        'generation':row.generation})


def issue(row,action):
    raw=secrets.token_urlsafe(32)
    expiry=timezone.now()+timedelta(hours=48) if action=='confirm' else timezone.now()+timedelta(days=365)
    token=SubscriptionToken.objects.create(subscription=row,token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        action=action,generation=row.generation,expires_at=expiry)
    return raw,token


def request_subscription(data):
    values={}
    for key,limit in [('email',254),('source',120),('source_url',1000),('evidence',1000)]:
        value=data.get(key,'')
        if not isinstance(value,str) or len(value)>limit:raise ValueError('Проверьте поля подписки и ограничения длины.')
        values[key]=value.strip()
    values['email']=canonical(values['email'])
    try:
        validate_email(values['email'])
        if values['source_url']:
            URLValidator(schemes=['http','https'])(values['source_url'])
            parsed=urlsplit(values['source_url'])
            if parsed.username or parsed.password:raise ValidationError('credentials')
    except (ValidationError,ValueError):raise ValueError('Проверьте email и HTTP(S)-ссылку источника.')
    if not values['source']:raise ValueError('Укажите источник запроса подписки.')
    is_test=data.get('is_test',True)
    if type(is_test) is not bool:raise ValueError('Выберите тестовую или рабочую запись.')
    now=timezone.now();source_at=data.get('source_at','')
    if source_at:
        if not isinstance(source_at,str) or len(source_at)>40:raise ValueError('Проверьте дату источника.')
        try:source_at=parse_datetime(source_at)
        except ValueError:source_at=None
        if not source_at:raise ValueError('Проверьте дату источника.')
        if timezone.is_naive(source_at):source_at=timezone.make_aware(source_at)
        if source_at.year<1970 or source_at>now+timedelta(minutes=5):raise ValueError('Дата источника должна быть в прошлом или текущем времени.')
    else:source_at=now
    with transaction.atomic():
        row,_=EmailSubscription.objects.get_or_create(email=values['email'],scope=SCOPE,is_test=is_test,
            defaults={**values,'source_at':source_at,'consent_text':CONSENT_TEXT,'consent_version':CONSENT_VERSION})
        row=EmailSubscription.objects.select_for_update().get(pk=row.pk)
        if row.state=='confirmed' and row.consent_version==CONSENT_VERSION:raise ValueError('Подписка уже подтверждена. Её история сохранена; новое подтверждение не требуется.')
        SubscriptionToken.objects.filter(subscription=row,action='confirm',used_at=None,invalidated_at=None).update(invalidated_at=now)
        for key,value in values.items():setattr(row,key,value)
        row.source_at=source_at;row.state='pending';row.generation+=1;row.requested_at=now
        row.consent_text=CONSENT_TEXT;row.consent_version=CONSENT_VERSION;row.save()
        audit(row,'requested',row.source)
        raw,token=issue(row,'confirm')
    return row,raw,token


def unsubscribe_link(subscription_id):
    with transaction.atomic():
        row=EmailSubscription.objects.select_for_update().get(pk=subscription_id)
        raw,token=issue(row,'unsubscribe');audit(row,'link_created','Центр подписок: ссылка отписки')
    return row,raw,token


def restrictions(row,revoke):
    # Test records must never create addresses or change the real sending base.
    if row.is_test:return
    Address.objects.get_or_create(email=row.email)
    for address in Address.objects.select_for_update().filter(email__iexact=row.email).order_by('pk'):
        active=list(address.exclusions.filter(removed_at=None).order_by('reason'))
        known='; '.join(REASONS.get(e.reason,e.reason) for e in active)
        current=address.suppression_reason
        legacy='' if current==known else current[:-len('; '+known)] if known and current.endswith('; '+known) else current
        # A plain older opt-out is adopted; arbitrary older restrictions are preserved.
        if legacy==REASONS['unsubscribe']:legacy=''
        if revoke:
            EmailExclusion.objects.get_or_create(address=address,reason='unsubscribe',removed_at=None,
                defaults={'note':'Отписка через центр подписок.'})
        else:
            address.exclusions.filter(reason='unsubscribe',removed_at=None).update(removed_at=timezone.now(),removal_note='Новое согласие подтверждено по ссылке центра подписок.')
        labels=[REASONS.get(e.reason,e.reason) for e in address.exclusions.filter(removed_at=None).order_by('reason')]
        address.suppression_reason='; '.join(([legacy] if legacy else [])+labels)[:100]
        address.save(update_fields=['suppression_reason'])


def lookup(raw):
    if not isinstance(raw,str) or not 30<=len(raw)<=100:return None
    return SubscriptionToken.objects.select_related('subscription').filter(token_hash=hashlib.sha256(raw.encode()).hexdigest()).first()


def token_error(token):
    if not token:return 'Ссылка не найдена.'
    if token.invalidated_at or token.expires_at<=timezone.now():return 'Срок действия ссылки истёк или она заменена новой.'
    if token.action=='confirm' and not token.used_at and token.generation!=token.subscription.generation:return 'Подтверждение устарело. Нужна новая ссылка.'
    return ''


def apply(raw,agreed=False):
    initial=lookup(raw)
    if not initial:raise ValueError('Ссылка не найдена.')
    return apply_token(initial,agreed)


def apply_token(initial,agreed=False):
    """Internal token operation; public callers must resolve a bearer token first."""
    with transaction.atomic():
        row=EmailSubscription.objects.select_for_update().get(pk=initial.subscription_id)
        token=SubscriptionToken.objects.select_for_update().select_related('subscription').get(pk=initial.pk)
        error=token_error(token)
        if error:raise ValueError(error)
        now=timezone.now()
        if token.action=='confirm':
            if token.used_at:return row,False
            if not agreed:raise ValueError('Для подтверждения отметьте согласие на рассылку.')
            row.state='confirmed';row.confirmed_at=now;row.revoked_at=None
            restrictions(row,False);event='confirmed'
        else:
            if row.state=='revoked':
                from .mail_tracking import observe_unsubscribe
                observe_unsubscribe(token)
                return row,False
            row.state='revoked';row.revoked_at=now;row.generation+=1
            SubscriptionToken.objects.filter(subscription=row,action='confirm',used_at=None,invalidated_at=None).update(invalidated_at=now)
            restrictions(row,True);event='revoked'
        row.save();token.used_at=now;token.save(update_fields=['used_at'])
        audit(row,event,'Тестовая ссылка' if row.is_test else 'Подтверждение по ссылке' if event=='confirmed' else 'Отписка по ссылке')
        if event=='revoked':
            from .mail_tracking import observe_unsubscribe
            observe_unsubscribe(token)
    return row,True

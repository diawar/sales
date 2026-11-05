import uuid
from datetime import timedelta
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import Address,EmailCheckState
from .email_validation import check_email
from .collection import log

_cache={}
_cache_until=None


def candidates():
    return Address.objects.filter(archived=False,suppression_reason='').filter(
        Q(validation_checked_at__isnull=True)|Q(validation_next_at__lte=timezone.now()))


def run_round(checker=None):
    global _cache_until
    now=timezone.now();EmailCheckState.objects.get_or_create(pk=1)
    EmailCheckState.objects.filter(pk=1).update(heartbeat_at=now)
    token=uuid.uuid4().hex
    if not EmailCheckState.objects.filter(pk=1,enabled=True).filter(Q(lease_until__isnull=True)|Q(lease_until__lte=now)).update(lease=token,lease_until=now+timedelta(minutes=2)):return False
    try:
        address=candidates().order_by('validation_next_at','id').first()
        if not address:return False
        if not _cache_until or _cache_until<=now:
            _cache.clear();_cache_until=now+timedelta(minutes=5)
        result=(checker or check_email)(address.email,_cache)
        # Transient answers must actually be queried again on retry.
        if result['code']=='temporary':_cache.pop(result.get('domain'),None)
        with transaction.atomic():
            state=EmailCheckState.objects.select_for_update().get(pk=1)
            if not state.enabled or state.lease!=token:return False
            current=Address.objects.select_for_update().get(pk=address.pk)
            if current.archived or current.suppression_reason or current.email!=address.email:return False
            if current.validation_checked_at!=address.validation_checked_at:return False
            current.validation_attempts+=1
            current.validation=result['status'];current.validation_result=result;current.validation_checked_at=timezone.now()
            current.validation_next_at=(timezone.now()+timedelta(minutes=min(1440,5*2**min(current.validation_attempts-1,9)))) if result['code']=='temporary' else None
            current.save(update_fields=['validation_attempts','validation','validation_result','validation_checked_at','validation_next_at'])
            state.error='';state.save(update_fields=['error'])
            log(f'Проверка email: адрес #{current.pk}, {result["status"]}.','warning' if result['code']=='temporary' else 'info',source='email_check')
        return True
    except Exception:
        EmailCheckState.objects.filter(pk=1,lease=token).update(enabled=False,error='Ошибка проверки email. Очередь сохранена; проверьте журнал и перезапустите.')
        log('Проверка email: внутренняя ошибка, очередь приостановлена.','warning',source='email_check')
        return False
    finally:
        EmailCheckState.objects.filter(pk=1,lease=token).update(lease='',lease_until=None)

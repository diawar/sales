"""Resumable, read-only contact discovery. No sending code is called here."""
import time
import uuid
from datetime import timedelta
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import CollectionState,CollectionItem,CollectionParticipant,ParticipantOrigin,CollectionLog,Client,Address,Contact,Evidence,Audience,Phone
from .goszakup_client import PublicClient,SourceError,utc_datetime
from .goszakup_source import LeadPortal,ProtocolFormatError
from .phones import parse_phone
from .profiles import apply_profile

def log(message,level='info',source='goszakup'):
    CollectionLog.objects.create(message=message[:300],level=level,source=source)
    # Diagnostics are bounded; contact evidence is stored separately.
    cutoff=CollectionLog.objects.order_by('-id').values_list('id',flat=True)[1000:1001]
    if cutoff:
        CollectionLog.objects.filter(id__lte=list(cutoff)[0]).delete()

def configure(action,days=None):
    with transaction.atomic():
        state,_=CollectionState.objects.get_or_create(pk=1)
        if days is not None:
            if not isinstance(days,int) or isinstance(days,bool) or not 1<=days<=365:
                raise ValueError('Период: от 1 до 365 дней.')
            if state.lease_until and state.lease_until>timezone.now():
                raise ValueError('Сначала приостановите сбор и дождитесь завершения текущего шага.')
            state.lookback_days=days
            state.windows=[]
            state.next_scan_at=None
        state.enabled=action=='start'
        if days is not None:
            state.save(update_fields=['enabled','lookback_days','windows','next_scan_at'])
        else:
            CollectionState.objects.filter(pk=1).update(enabled=state.enabled)
        log('Сбор включён.' if state.enabled else 'Сбор приостановлен.')
    return state

def claim():
    CollectionState.objects.get_or_create(pk=1)
    now=timezone.now();token=uuid.uuid4().hex
    owned=CollectionState.objects.filter(pk=1,enabled=True).filter(Q(lease_until__isnull=True)|Q(lease_until__lte=now)).update(lease=token,lease_until=now+timedelta(minutes=10))
    if not owned:return None
    # A process may have been killed after claiming an item.
    for Model in (CollectionItem,CollectionParticipant):
        Model.objects.filter(state='processing').update(state='retry',next_attempt_at=now,error='Предыдущий шаг прерван; повторяем с сохранённого места.')
    return token

def guard(token):
    now=timezone.now()
    if not CollectionState.objects.filter(pk=1,enabled=True,lease=token,lease_until__gt=now).update(lease_until=now+timedelta(minutes=10),heartbeat_at=now):
        raise SourceError('Сбор приостановлен или шаг передан другому процессу.')

def pace(token):
    guard(token)
    state=CollectionState.objects.get(pk=1)
    delay=max(0,(state.next_request_at-timezone.now()).total_seconds()) if state.next_request_at else 0
    if delay>2:
        exc=SourceError('Источник запросил паузу; следующий запрос будет выполнен позже.')
        exc.retry_after=delay
        raise exc
    if delay:time.sleep(min(delay,60))
    guard(token)
    CollectionState.objects.filter(pk=1,lease=token).update(next_request_at=timezone.now()+timedelta(seconds=1))

def failure(exc):
    return str(exc)[:300] if isinstance(exc,SourceError) else 'Не удалось обработать источник. Требуется проверка формата.'

def delay(exc,attempts):
    seconds=max(60,min(86400,60*2**min(attempts,8)),getattr(exc,'retry_after',0))
    return timezone.now()+timedelta(seconds=seconds)

def cooldown(exc):
    seconds=getattr(exc,'retry_after',0)
    if seconds>0:
        until=timezone.now()+timedelta(seconds=seconds)
        CollectionState.objects.filter(pk=1).filter(Q(next_request_at__isnull=True)|Q(next_request_at__lt=until)).update(next_request_at=until)

def cooling():
    return CollectionState.objects.filter(pk=1,next_request_at__gt=timezone.now()+timedelta(seconds=2)).exists()

def discover(api,token):
    state=CollectionState.objects.get(pk=1)
    if state.next_scan_at and state.next_scan_at>timezone.now():return False
    if not state.windows:
        now=timezone.now()
        state.windows=[{'lo':(now-timedelta(days=i+1)).isoformat(),'hi':(now-timedelta(days=i)).isoformat(),'offset':0} for i in range(state.lookback_days)]
    window=state.windows[0];lo=utc_datetime(window['lo']);hi=utc_datetime(window['hi'])
    page=api.search('announcements',system_id__in='1__4',results_publication_date__gte=window['lo'],results_publication_date__lte=window['hi'],ordering='-publish_date',limit=100,offset=window['offset'])
    for row in page.results:
        published=utc_datetime(row.get('results_publication_date'))
        if not published or not lo<=published<=hi or row.get('system_id') not in {1,4} or isinstance(row.get('external_id'),bool) or not isinstance(row.get('external_id'),int) or row['external_id']<=0:
            raise SourceError('Источник не подтвердил фильтр периода/площадки. Курсор не сдвинут.')
    guard(token)
    with transaction.atomic():
        if page.count>9000:
            if hi-lo<=timedelta(seconds=1):raise SourceError('Слишком плотное окно результатов; требуется проверка.')
            middle=lo+(hi-lo)/2
            state.windows[:1]=[{'lo':middle.isoformat(),'hi':window['hi'],'offset':0},{'lo':window['lo'],'hi':middle.isoformat(),'offset':0}]
        else:
            for row in page.results:
                item,created=CollectionItem.objects.get_or_create(external_id=row['external_id'],defaults={'published_at':utc_datetime(row['results_publication_date'])})
                if not created and item.published_at!=utc_datetime(row['results_publication_date']):
                    CollectionItem.objects.filter(pk=item.pk).update(published_at=utc_datetime(row['results_publication_date']),state='queued',attempts=0,error='',next_attempt_at=timezone.now())
            if page.next_url:
                if not page.results:raise SourceError('Пустая страница с продолжением.')
                window['offset']+=max(1,len(page.results)-5)
                if window['offset']+100>10000:raise SourceError('Лимит страницы источника; требуется повтор окна.')
            else:state.windows.pop(0)
        values={'windows':state.windows,'next_scan_at':None,'error':''}
        if not state.windows:
            values.update(completed_at=timezone.now(),next_scan_at=timezone.now()+timedelta(hours=6))
            log('Обход заданного периода завершён. Следующая проверка через 6 часов.')
        CollectionState.objects.filter(pk=1,lease=token).update(**values)
    return True

def collect_protocol(portal,token):
    item=CollectionItem.objects.filter(state__in=['queued','retry'],next_attempt_at__lte=timezone.now()).order_by('-published_at','id').first()
    if item is None:return False
    item.state='processing';item.attempts+=1;item.save(update_fields=['state','attempts','updated_at'])
    try:
        participants,unresolved,url=portal.protocol(item.external_id)
        guard(token)
        with transaction.atomic():
            for person in participants:
                participant,_=CollectionParticipant.objects.get_or_create(identifier=person.identifier,defaults={'name':person.name[:300]})
                if participant.next_attempt_at<=timezone.now() and participant.state=='done':
                    participant.state='queued';participant.save(update_fields=['state'])
                ParticipantOrigin.objects.update_or_create(participant=participant,item=item,defaults={'protocol_url':url})
            item.state='review' if unresolved else 'done';item.protocol_url=url
            item.error=f'Не распознано строк: {unresolved}.' if unresolved else ''
            item.save(update_fields=['state','protocol_url','error','updated_at'])
            log(f'Разобран источник {item.external_id}: участников {len(participants)}, строк для проверки {unresolved}.')
    except Exception as exc:
        cooldown(exc)
        item.state='review' if isinstance(exc,ProtocolFormatError) or item.attempts>=5 else 'retry'
        item.error=failure(exc);item.next_attempt_at=delay(exc,item.attempts);item.save(update_fields=['state','error','next_attempt_at','updated_at'])
        log(item.error,'warning')
    return True

@transaction.atomic
def store_contact(participant,value):
    client,_=Client.objects.get_or_create(identity_key='kz:'+participant.identifier,defaults={'name':(value.get('name') or participant.name)[:300],'identifier':participant.identifier})
    apply_profile(client,{'name':(value.get('name') or participant.name)[:300]},'Goszakup',value['source_url'])
    audience=Audience.objects.get(parent__isnull=True,name='Бизнес')
    contacts=[]
    Evidence.objects.filter(contact__client=client,source='Goszakup').exclude(contact__address__email__in=value['emails']).update(current=False)
    for email in value['emails']:
        address,_=Address.objects.get_or_create(email=email)
        contact=Contact.objects.filter(client=client,address=None).first()
        if contact:
            contact.address=address;contact.save(update_fields=['address'])
        else:contact,_=Contact.objects.get_or_create(client=client,address=address)
        contacts.append(contact)
    if not contacts:
        if not Contact.objects.filter(client=client,address__isnull=False).exists():
            contact,_=Contact.objects.get_or_create(client=client,address=None)
            contacts.append(contact)
    for contact in contacts:
        contact.audiences.add(audience)
        Evidence.objects.update_or_create(contact=contact,source='Goszakup',source_url=value['source_url'],defaults={'current':True})
        for origin in participant.origins.all():
            Evidence.objects.update_or_create(contact=contact,source='Goszakup',source_url=origin.protocol_url,defaults={'current':True})
    for raw in value.get('phones',[]):
        phone=parse_phone(raw)
        Phone.objects.update_or_create(client=client,number=phone['number'],source='Goszakup',source_url=value['source_url'],defaults={'raw':phone['raw'],'kind':phone['kind']})

def collect_contact(portal,token):
    participant=CollectionParticipant.objects.filter(state__in=['queued','retry'],next_attempt_at__lte=timezone.now()).order_by('next_attempt_at','id').first()
    if participant is None:return False
    participant.state='processing';participant.attempts+=1;participant.save(update_fields=['state','attempts','updated_at'])
    try:
        value=portal.contacts(participant.identifier)
        guard(token)
        with transaction.atomic():
            store_contact(participant,value)
            participant.state='done';participant.error='';participant.attempts=0;participant.next_attempt_at=timezone.now()+timedelta(days=30 if value['emails'] else 7)
            participant.save(update_fields=['state','error','attempts','next_attempt_at','updated_at'])
            log(f'Контакты сохранены: email {len(value["emails"])}, телефонов {len(value.get("phones",[]))}.')
    except Exception as exc:
        cooldown(exc)
        participant.state='review' if participant.attempts>=5 else 'retry';participant.error=failure(exc);participant.next_attempt_at=delay(exc,participant.attempts)
        participant.save(update_fields=['state','error','next_attempt_at','updated_at']);log(participant.error,'warning')
    return True

def run_round(api_factory=None):
    state,_=CollectionState.objects.get_or_create(pk=1)
    CollectionState.objects.filter(pk=1).update(heartbeat_at=timezone.now())
    token=claim()
    if not token:return False
    changed=False
    try:
        if cooling():return False
        factory=api_factory or (lambda:PublicClient(pace=lambda:pace(token)))
        with factory() as api:
            if CollectionItem.objects.filter(state__in=['queued','retry']).count()<100:
                try:changed=discover(api,token) or changed
                except Exception as exc:
                    cooldown(exc)
                    CollectionState.objects.filter(pk=1,lease=token).update(error=failure(exc),next_scan_at=delay(exc,1));log(failure(exc),'warning')
            portal=LeadPortal(api)
            if not cooling() and CollectionState.objects.filter(pk=1,lease=token,enabled=True).exists():changed=collect_protocol(portal,token) or changed
            # Recheck published cards periodically, not only after a new protocol.
            CollectionParticipant.objects.filter(state='done',next_attempt_at__lte=timezone.now()).update(state='queued')
            if not cooling() and CollectionState.objects.filter(pk=1,lease=token,enabled=True).exists():changed=collect_contact(portal,token) or changed
        return changed
    finally:
        CollectionState.objects.filter(pk=1,lease=token).update(lease='',lease_until=None)

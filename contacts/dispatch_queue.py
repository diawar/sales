"""Persistent simulation queue. This module has NO mail, SMTP, HTTP or DNS transport."""
from datetime import timedelta
from django.db import transaction
from django.db.models import Q, Min, Count
from django.utils import timezone
from .models import Address, Contact, CampaignQueue, CampaignQueueItem, CampaignQueueAttempt, CampaignQueueWorker
from .email_policy import sending_state
from .campaign_views import audience_selection
from . import action_audit as audit

SCENARIOS = {'success':'Без ошибок', 'temporary_once':'Временная ошибка на первой попытке',
             'temporary_always':'Постоянные временные ошибки до лимита', 'permanent':'Окончательная ошибка'}
ACTIVE_ITEMS = ('pending','retry')


def settings(data):
    values = {}
    for key, default, low, high in [('rate_per_minute',30,1,600),('max_attempts',3,1,10),('retry_seconds',60,1,3600)]:
        value=data.get(key,default)
        if type(value) is not int or not low <= value <= high: raise ValueError(f'{key}: от {low} до {high}.')
        values[key]=value
    scenario=data.get('scenario','success')
    if not isinstance(scenario,str) or scenario not in SCENARIOS: raise ValueError('Выберите сценарий имитации.')
    if data.get('mode','simulation') != 'simulation': raise ValueError('Доступна только имитация без отправки.')
    values['scenario']=scenario
    return values


def manifest(campaign):
    _, expanded=audience_selection({'audience_ids':list(campaign.audiences.values_list('pk',flat=True))})
    contacts=Contact.objects.filter(audiences__pk__in=expanded)
    selected={email.strip().casefold():pk for pk,email in Address.objects.filter(pk__in=contacts.values('address_id')).values_list('pk','email')}
    from .subscriptions import revoked_emails
    blocked=set(revoked_emails())
    for address in Address.objects.only('email','archived','suppression_reason','validation_result','validation').iterator(chunk_size=1000):
        email=address.email.strip().casefold()
        if email in selected and sending_state(address)['code']!='ready': blocked.add(email)
    return [(email,pk) for email,pk in sorted(selected.items()) if email not in blocked]


@transaction.atomic
def prepare(campaign,data):
    from .campaign_approval import require_approved
    approval_version=require_approved(campaign,recipients=True)
    if type(data.get('campaign_revision')) is not int or data['campaign_revision']!=campaign.revision:
        raise ValueError('Черновик изменился. Обновите список рассылок.')
    existing=CampaignQueue.objects.filter(campaign=campaign).first()
    if existing: return existing
    values=settings(data)
    recipients=manifest(campaign)
    if not recipients: raise ValueError('После исключений нет получателей. Проверьте аудитории и email.')
    now=timezone.now()
    row=CampaignQueue.objects.create(campaign=campaign,**values,
        ready_at=campaign.scheduled_at if campaign.schedule_mode=='scheduled' else now,
        snapshot={'approval_version':approval_version,'campaign_revision':campaign.revision,'name':campaign.name,'template':campaign.template_snapshot,
                  'sender':campaign.sender_snapshot,'timezone':campaign.schedule_timezone,'mode':'simulation'})
    CampaignQueueItem.objects.bulk_create([CampaignQueueItem(queue=row,address_id=pk,email=email,next_attempt_at=now) for email,pk in recipients],batch_size=500)
    audit.record('prepared','queue',row.pk,campaign.name,campaigns=[campaign.pk],
        after=queue_snapshot(row),summary='Подготовлена только очередь имитации. Письма не отправлялись.')
    return row


def queue_snapshot(row):
    return {k:getattr(row,k) for k in ('campaign_id','state','rate_per_minute','max_attempts','retry_seconds','scenario','ready_at')}


@transaction.atomic
def control(row,action,data):
    before=queue_snapshot(row)
    if action=='configure':
        if row.state!='paused': raise ValueError('Настройки можно менять только на паузе.')
        values=settings({key:getattr(row,key) for key in ('rate_per_minute','max_attempts','retry_seconds','scenario')}|data)
        if row.items.filter(attempts__gt=0).exists() and values['scenario']!=row.scenario:
            raise ValueError('После начала обработки сценарий имитации изменить нельзя.')
        for key,value in values.items(): setattr(row,key,value)
        if row.last_processed_at:
            row.next_slot_at=max(row.next_slot_at or row.last_processed_at,row.last_processed_at+timedelta(seconds=60/row.rate_per_minute))
    elif action=='pause':
        if row.state not in ('running','paused'): raise ValueError('Завершённую или отменённую очередь нельзя поставить на паузу.')
        row.state='paused'
    elif action=='resume':
        from .campaign_approval import require_approved
        require_approved(row.campaign,version=row.snapshot.get('approval_version',-1))
        if row.state not in ('paused','running'): raise ValueError('Завершённую или отменённую очередь нельзя продолжить.')
        row.state='running'
    elif action=='cancel':
        if row.state=='completed': raise ValueError('Очередь уже завершена.')
        if row.state!='cancelled':
            row.items.filter(state__in=ACTIVE_ITEMS).update(state='cancelled',finished_at=timezone.now(),detail='Отменено оператором. Письмо не отправлялось.')
        row.state='cancelled'
    else: raise ValueError('Неизвестное действие очереди.')
    row.save()
    after=queue_snapshot(row)
    if before!=after:
        kind={'configure':'settings_changed','pause':'paused','resume':'resumed','cancel':'cancelled'}[action]
        if action=='resume' and not row.last_processed_at and not audit.ActionAuditEntry.objects.filter(object_type='queue',object_id=row.pk,action__in=('started','resumed'),result='success').exists():kind='started'
        audit.record(kind,'queue',row.pk,row.snapshot.get('name',''),campaigns=[row.campaign_id],before=before,after=after,
            summary='Управление очередью имитации. Отправки не было.')


def worker_alive():
    return CampaignQueueWorker.objects.filter(pk=1,heartbeat_at__gte=timezone.now()-timedelta(seconds=30)).exists()


def report(row):
    counts={key:0 for key in ('pending','retry','simulated','failed','skipped','cancelled')}
    counts.update({r['state']:r['total'] for r in row.items.values('state').annotate(total=Count('pk'))})
    history=CampaignQueueAttempt.objects.filter(item__queue=row).select_related('item').order_by('-pk')[:20]
    return {'id':row.pk,'state':row.state,'mode':'simulation','rate_per_minute':row.rate_per_minute,
            'max_attempts':row.max_attempts,'retry_seconds':row.retry_seconds,'scenario':row.scenario,
            'ready_at':row.ready_at,'next_slot_at':row.next_slot_at if row.state in ('paused','running') else None,'timezone':row.snapshot.get('timezone','Asia/Qyzylorda'),
            'campaign_revision':row.snapshot.get('campaign_revision'),'approval_stop':row.snapshot.get('approval_stop',''),'stats':counts,'total':sum(counts.values()),
            'history':[{'email':h.item.email,'number':h.number,'outcome':h.outcome,'created_at':h.created_at} for h in history]}


def run_round(now=None):
    now=now or timezone.now()
    CampaignQueueWorker.objects.update_or_create(pk=1,defaults={'heartbeat_at':now})
    with transaction.atomic():
        row=CampaignQueue.objects.select_for_update().filter(state='running',ready_at__lte=now).filter(
            Q(next_slot_at__isnull=True)|Q(next_slot_at__lte=now)).order_by('next_slot_at','id').first()
        if not row: return False
        from .campaign_approval import require_approved
        try:require_approved(row.campaign,version=row.snapshot.get('approval_version',-1))
        except ValueError as exc:
            row.state='paused';row.snapshot['approval_stop']=str(exc);row.save()
            audit.record('auto_stopped','queue',row.pk,row.snapshot.get('name',''),campaigns=[row.campaign_id],
                summary=str(exc),automatic=True)
            return False
        item=row.items.select_for_update().filter(state__in=ACTIVE_ITEMS,next_attempt_at__lte=now).order_by('next_attempt_at','id').first()
        if not item:
            due=row.items.filter(state__in=ACTIVE_ITEMS).aggregate(due=Min('next_attempt_at'))['due']
            if due: row.next_slot_at=due
            else: row.state='completed'
            row.save()
            if row.state=='completed':audit.record('completed','queue',row.pk,row.snapshot.get('name',''),campaigns=[row.campaign_id],
                after={'state':row.state},automatic=True,summary='Очередь имитации завершена. Реальной отправки не было.')
            return False
        current=Address.objects.get(pk=item.address_id)
        # Recheck exclusions after preparation, including other case variants.
        variants=Address.objects.filter(Q(pk=current.pk)|Q(email__iexact=item.email))
        from .subscriptions import has_revocation
        allowed=not has_revocation(item.email) and current.email.strip().casefold()==item.email and all(sending_state(a)['code']=='ready' for a in variants)
        if not allowed:
            item.state='skipped';item.detail='Исключён при повторной проверке допуска. Письмо не отправлялось.';item.finished_at=now
        elif item.attempts>=row.max_attempts:
            item.state='failed';item.detail='Лимит попыток имитации исчерпан.';item.finished_at=now
        else:
            item.attempts+=1
            temporary=row.scenario=='temporary_always' or row.scenario=='temporary_once' and item.attempts==1
            outcome='temporary' if temporary else 'permanent' if row.scenario=='permanent' else 'simulated'
            CampaignQueueAttempt.objects.create(item=item,number=item.attempts,outcome=outcome)
            if temporary and item.attempts<row.max_attempts:
                item.state='retry';item.next_attempt_at=now+timedelta(seconds=min(86400,row.retry_seconds*2**(item.attempts-1)))
                item.detail='Временная ошибка имитации; назначен повтор.'
            else:
                item.state='simulated' if outcome=='simulated' else 'failed';item.finished_at=now
                item.detail='Обработано в имитации. Письмо не отправлялось.' if outcome=='simulated' else 'Ошибка имитации. Реальный адрес не блокировался.'
        item.save()
        row.last_processed_at=now
        row.next_slot_at=now+timedelta(seconds=60/row.rate_per_minute)
        if not row.items.filter(state__in=ACTIVE_ITEMS).exists(): row.state='completed'
        row.save()
        if row.state=='completed':audit.record('completed','queue',row.pk,row.snapshot.get('name',''),campaigns=[row.campaign_id],
            after={'state':row.state},automatic=True,summary='Очередь имитации завершена. Реальной отправки не было.')
        return True

"""Automatic manufacturer collection with atomic per-page saves and cursor."""
import uuid
from datetime import timedelta
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import ProducerPilot,Client,Audience
from .profiles import apply_profile,attach_contacts,fact
from .eondiris_source import read_page
from .eondiris_source import normalize
from .goszakup_client import SourceError
from .collection import log

def reset_cycle(state):
    state.token=uuid.uuid4();state.next_page=1;state.rows=[];state.issues=[];state.report={'mode':'automatic'};state.cycle_seen={};state.result={};state.error='';state.started_at=timezone.now();state.completed_at=None;state.next_attempt_at=None;state.attempts=0

@transaction.atomic
def control(action,pages=None):
    ProducerPilot.objects.get_or_create(pk=1)
    state=ProducerPilot.objects.select_for_update().get(pk=1)
    if action=='start':
        if state.report.get('mode')!='automatic':reset_cycle(state)
        state.enabled=True
        state.status='waiting' if state.next_attempt_at and state.next_attempt_at>timezone.now() else 'queued'
        state.error=''
        message='e-ondiris: автоматический сбор включён.'
    elif action=='pause':
        state.enabled=False;state.status='paused';message='e-ondiris: сбор приостановлен, курсор сохранён.'
    else:raise ValueError('Выберите включение или паузу автоматического сбора.')
    state.save();log(message,source='eondiris')
    return state

def save_rows(rows):
    parent=Audience.objects.get(name='Бизнес',parent=None)
    category,_=Audience.objects.get_or_create(name_key=str(parent.id)+':производители',defaults={'name':'Производители','parent':parent})
    created=set();updated=set();conflicts=set()
    for row in rows:
        client,new=Client.objects.get_or_create(identity_key='kz:'+row['identifier'],defaults={'name':row['name'],'identifier':row['identifier']})
        (created if new else updated).add(client.id)
        for field in apply_profile(client,row['profile'],'e-ondiris',row['source_url']):conflicts.add(f'{client.id}:{field}')
        attach_contacts(client,row['emails'],[p['raw'] for p in row['phones']],[category],'e-ondiris',row['source_url'])
        for field,value in row['source_fields'].items():
            if value:fact(client,field,value,'e-ondiris',row['source_url'],'evidence')
    return created,updated,conflicts

def run_round(reader=None):
    ProducerPilot.objects.get_or_create(pk=1)
    now=timezone.now();ProducerPilot.objects.filter(pk=1).update(heartbeat_at=now)
    token=uuid.uuid4().hex
    owned=ProducerPilot.objects.filter(pk=1,enabled=True,status__in=['queued','fetching','waiting','failed']).filter(Q(lease_until__isnull=True)|Q(lease_until__lte=now)).filter(Q(next_attempt_at__isnull=True)|Q(next_attempt_at__lte=now)).update(lease=token,lease_until=now+timedelta(minutes=3),status='fetching')
    if not owned:return False
    state=ProducerPilot.objects.get(pk=1)
    try:
        if state.completed_at:
            with transaction.atomic():
                current=ProducerPilot.objects.select_for_update().get(pk=1)
                if current.lease!=token or not current.enabled:return False
                reset_cycle(current);current.status='fetching';current.save()
                state=current
        payload=(reader or read_page)(state.next_page)
        rows=[];issues=[];inactive=0
        for number,raw in enumerate(payload['data'],1):
            try:
                row,problems=normalize(raw)
                if row:rows.append(row)
                else:inactive+=1
                issues.extend({'page':state.next_page,'row':number,'message':problem} for problem in problems)
            except ValueError as exc:issues.append({'page':state.next_page,'row':number,'message':str(exc)})
        if payload['data'] and not rows and not inactive:raise SourceError('Страница не распознана; данные и курсор не изменены.')
        with transaction.atomic():
            current=ProducerPilot.objects.select_for_update().get(pk=1)
            if current.lease!=token or current.status!='fetching' or not current.enabled:return False
            created,updated,conflicts=save_rows(rows) if rows else (set(),set(),set())
            seen=current.cycle_seen
            additions={'organizations':{r['identifier'] for r in rows},'emails':{e for r in rows for e in r['emails']},'phones':{p['number'] for r in rows for p in r['phones']},'without_email':{r['identifier'] for r in rows if not r['emails']},'with_email':{r['identifier'] for r in rows if r['emails']},'created':created,'updated':updated,'conflicts':conflicts}
            for key,values in additions.items():seen[key]=sorted(set(seen.get(key,[]))|values)
            previous=current.report
            valid=previous.get('rows',0)+len(rows)
            current.report={'mode':'automatic','raw_rows':previous.get('raw_rows',0)+len(payload['data']),'rows':valid,'organizations':len(seen['organizations']),'duplicate_rows':valid-len(seen['organizations']),'unique_emails':len(seen['emails']),'unique_phones':len(seen['phones']),'without_email':len(set(seen['without_email'])-set(seen['with_email'])),'created_clients':len(seen['created']),'updated_clients':len(set(seen['updated'])-set(seen['created'])),'conflicts':len(seen['conflicts']),'issues':previous.get('issues',0)+len(issues),'inactive_rows':previous.get('inactive_rows',0)+inactive,'source_meta':payload['meta']}
            current.cycle_seen=seen;current.issues=(current.issues+issues)[-100:];current.rows=[];current.next_page+=1;current.error='';current.attempts=0
            done=not payload['meta']['hasNextPage'];current.status='waiting' if done else 'queued';current.completed_at=timezone.now() if done else None;current.next_attempt_at=timezone.now()+timedelta(hours=6) if done else timezone.now()+timedelta(seconds=2)
            current.save()
            log(f'e-ondiris: страница {state.next_page} сохранена. Создано {len(created)}, дополнено {len(updated-created)}.'+(' Обход завершён; повтор через 6 часов.' if done else ''),source='eondiris')
        return True
    except Exception as exc:
        current=ProducerPilot.objects.get(pk=1)
        retry=max(60*2**min(current.attempts,8),getattr(exc,'retry_after',60))
        changed=ProducerPilot.objects.filter(pk=1,lease=token,status='fetching',enabled=True).update(status='failed',attempts=current.attempts+1,error=str(exc)[:300] if isinstance(exc,SourceError) else 'Ошибка обработки страницы. Курсор и данные сохранены.',next_attempt_at=timezone.now()+timedelta(seconds=retry))
        if changed:log(str(exc)[:250] if isinstance(exc,SourceError) else 'e-ondiris: ошибка обработки страницы.','warning',source='eondiris')
        return False
    finally:ProducerPilot.objects.filter(pk=1,lease=token).update(lease='',lease_until=None)

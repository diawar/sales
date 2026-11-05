"""DFO collection with per-page transactions, resumable cursor and daily cycles."""
import uuid
from datetime import timedelta
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import DfoCollection,Client,Audience
from .profiles import apply_profile,attach_contacts,fact
from .dfo_source import read_page
from .collection import log
from .goszakup_client import SourceError


@transaction.atomic
def control(action,mode=None):
    DfoCollection.objects.get_or_create(pk=1)
    state=DfoCollection.objects.select_for_update().get(pk=1)
    if action=='start':
        if mode is not None and mode not in {'pilot','automatic'}:raise ValueError('Некорректный режим сбора.')
        if mode and mode!=state.mode:
            if state.lease_until and state.lease_until>timezone.now():raise ValueError('Дождитесь завершения запроса.')
            # Continue after the accepted pilot; its two pages and statistics
            # remain part of the first complete cycle.
            state.mode=mode;state.completed_at=None;state.next_attempt_at=None
            if mode=='pilot':reset_cycle(state)
        elif state.completed_at and state.mode=='pilot':
            if state.lease_until and state.lease_until>timezone.now():raise ValueError('Дождитесь завершения запроса.')
            reset_cycle(state)
        state.enabled=True
        state.attempts=0;state.error=''
        message='ДФО: автоматический обход каталога включён.' if state.mode=='automatic' else 'ДФО: пилот до двух страниц включён.'
    elif action=='pause':state.enabled=False;message='ДФО: пауза, курсор сохранён.'
    else:raise ValueError('Выберите включение или паузу.')
    state.save();log(message,source='dfo');return state


def reset_cycle(state):
    state.next_page=1;state.completed_at=None;state.report={};state.seen={};state.next_attempt_at=None;state.error='';state.attempts=0


def root_name(row):
    legal=row['source_fields'].get('dfo_legal_form','').casefold()
    if 'некоммерческ' in legal:return 'Некоммерческие организации'
    if legal.startswith(('госпредприятие','государственное предприятие','госучреждение','государственное учреждение')):
        return 'Государственные структуры'
    return 'Бизнес'


def save_rows(rows):
    categories={}
    created=set();updated=set();conflicts=set()
    for row in rows:
        name=root_name(row)
        if name not in categories:
            root=Audience.objects.get(name=name,parent=None)
            categories[name],_=Audience.objects.get_or_create(name_key=f'{root.id}:дфо',defaults={'name':'ДФО','parent':root})
        category=categories[name]
        client,new=Client.objects.get_or_create(identity_key='kz:'+row['identifier'],defaults={'name':row['name'],'identifier':row['identifier']})
        (created if new else updated).add(client.id)
        for key in apply_profile(client,row['profile'],'ДФО',row['source_url']):conflicts.add(f'{client.id}:{key}')
        attach_contacts(client,row['emails'],[p['raw'] for p in row['phones']],[category],'ДФО',row['source_url'])
        for key,value in row['source_fields'].items():
            if value:fact(client,key,value,'ДФО',row['source_url'],'evidence')
    return created,updated,conflicts


def run_round(reader=None):
    DfoCollection.objects.get_or_create(pk=1)
    now=timezone.now();DfoCollection.objects.filter(pk=1).update(heartbeat_at=now)
    token=uuid.uuid4().hex
    owned=DfoCollection.objects.filter(pk=1,enabled=True).filter(Q(lease_until__isnull=True)|Q(lease_until__lte=now)).filter(Q(next_attempt_at__isnull=True)|Q(next_attempt_at__lte=now)).update(lease=token,lease_until=now+timedelta(minutes=2))
    if not owned:return False
    try:
        state=DfoCollection.objects.get(pk=1)
        if state.completed_at and state.mode=='automatic':
            with transaction.atomic():
                current=DfoCollection.objects.select_for_update().get(pk=1)
                if current.lease!=token or not current.enabled:return False
                reset_cycle(current);current.save();state=current
        payload=(reader or read_page)(state.next_page)
        with transaction.atomic():
            current=DfoCollection.objects.select_for_update().get(pk=1)
            if current.lease!=token or not current.enabled:return False
            created,updated,conflicts=save_rows(payload['rows'])
            seen=current.seen
            additions={'bins':{r['identifier'] for r in payload['rows']},'created':created,'updated':updated,'conflicts':conflicts,
                'emails':{e for r in payload['rows'] for e in r['emails']},'phones':{p['number'] for r in payload['rows'] for p in r['phones']}}
            for key,values in additions.items():seen[key]=sorted(set(seen.get(key,[]))|values)
            current.seen=seen;previous=current.report
            current.report={'organizations':len(seen['bins']),'created':len(seen['created']),'updated':len(set(seen['updated'])-set(seen['created'])),
                'emails':len(seen['emails']),'phones':len(seen['phones']),'conflicts':len(seen['conflicts']),
                'issues':previous.get('issues',0)+len(payload['issues']),'processed_pages':previous.get('processed_pages',0)+1,'catalog_total':payload['total']}
            current.next_page+=1;current.error='';current.attempts=0
            done=not payload['has_next'] or (current.mode=='pilot' and current.next_page>2)
            if done:
                current.completed_at=timezone.now()
                if current.mode=='pilot':current.enabled=False
            current.next_attempt_at=timezone.now()+(timedelta(hours=24) if done and current.mode=='automatic' else timedelta(seconds=10));current.save()
            ending=(' Обход завершён; обновление через 24 часа.' if current.mode=='automatic' else ' Пилот завершён; сбор остановлен.') if done else ''
            log(f'ДФО: страница {state.next_page} сохранена, создано {len(created)}, дополнено {len(updated-created)}.'+ending,source='dfo')
            for issue in payload['issues']:
                log('ДФО: '+issue,'warning',source='dfo')
        return True
    except Exception as exc:
        with transaction.atomic():
            current=DfoCollection.objects.select_for_update().get(pk=1)
            if current.lease!=token or not current.enabled:return False
            current.attempts+=1;current.error=str(exc)[:300] if isinstance(exc,SourceError) else 'ДФО: ошибка обработки; данные и курсор сохранены.'
            current.next_attempt_at=timezone.now()+timedelta(seconds=max(60*2**min(current.attempts,8),getattr(exc,'retry_after',60)))
            if current.attempts>=3:current.enabled=False
            current.save();log(current.error,'warning',source='dfo')
        return False
    finally:DfoCollection.objects.filter(pk=1,lease=token).update(lease='',lease_until=None)

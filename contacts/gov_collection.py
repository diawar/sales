"""Small explicit-list Gov.kz pilot, resumable and atomic per organization."""
import uuid
from datetime import timedelta
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import GovCollection,Audience,Client
from .gov_source import canonical_url,read_card,ContactsNotPublished
from .profiles import apply_profile,attach_contacts,fact
from .collection import log
from .goszakup_client import SourceError

@transaction.atomic
def control(action,urls=None):
    GovCollection.objects.get_or_create(pk=1)
    state=GovCollection.objects.select_for_update().get(pk=1)
    if action in {'configure','append'}:
        if state.enabled or (state.lease_until and state.lease_until>timezone.now()):raise ValueError('Сначала приостановите сбор и дождитесь окончания запроса.')
        if not isinstance(urls,list) or not 1<=len(urls)<=500 or any(not isinstance(u,str) for u in urls):raise ValueError('Укажите от 1 до 500 адресов карточек.')
        normalized=list(dict.fromkeys(canonical_url(u.strip()) for u in urls))
        if action=='append':
            combined=list(dict.fromkeys(state.urls+normalized))
            if len(combined)>500:raise ValueError('В очереди допустимо до 500 карточек.')
            added=len(combined)-len(state.urls)
            state.urls=combined
            if added and state.completed_at:
                state.completed_at=None;state.next_attempt_at=None
            message=f'Gov.kz: добавлено карточек {added}; прогресс сохранён.'
        else:
            state.urls=normalized;state.cursor=0;state.report={};state.seen={};state.completed_at=None;state.next_attempt_at=None;state.error='';state.attempts=0
            message='Gov.kz: список карточек сохранён.'
    elif action=='start':state.enabled=True;message='Gov.kz: сбор включён.'
    elif action=='pause':state.enabled=False;message='Gov.kz: сбор приостановлен; курсор сохранён.'
    else:raise ValueError('Неизвестное действие.')
    state.save();log(message,source='gov');return state

@transaction.atomic
def save_card(row):
    parent=Audience.objects.get(name='Государственные структуры',parent=None)
    category,_=Audience.objects.get_or_create(name_key=f'{parent.id}:gov.kz',defaults={'name':'Gov.kz','parent':parent})
    client,new=Client.objects.get_or_create(identity_key=row['identity_key'],defaults={'name':row['name']})
    conflicts=apply_profile(client,{'name':row['name'],'city':row['city'],'websites':[canonical_url(row['source_url'])]},'Gov.kz',row['source_url'])
    attach_contacts(client,row['emails'],[p['raw'] for p in row['phones']],[category],'Gov.kz',row['source_url'])
    for item in row['source_rows']:fact(client,'gov_contact',item,'Gov.kz',row['source_url'],'evidence')
    for item in row['phones']:fact(client,'phone_purpose',{'number':item['number'],'purpose':item['purpose']},'Gov.kz',row['source_url'],'evidence')
    return client,new,conflicts

def run_round(reader=None):
    GovCollection.objects.get_or_create(pk=1)
    now=timezone.now();GovCollection.objects.filter(pk=1).update(heartbeat_at=now)
    token=uuid.uuid4().hex
    owned=GovCollection.objects.filter(pk=1,enabled=True).filter(Q(lease_until__isnull=True)|Q(lease_until__lte=now)).filter(Q(next_attempt_at__isnull=True)|Q(next_attempt_at__lte=now)).update(lease=token,lease_until=now+timedelta(minutes=2))
    if not owned:return False
    state=GovCollection.objects.get(pk=1)
    try:
        if state.completed_at:
            with transaction.atomic():
                state=GovCollection.objects.select_for_update().get(pk=1)
                if not state.enabled or state.lease!=token:return False
                state.cursor=0;state.report={};state.seen={};state.completed_at=None;state.save()
        row=(reader or read_card)(state.urls[state.cursor])
        with transaction.atomic():
            current=GovCollection.objects.select_for_update().get(pk=1)
            if not current.enabled or current.lease!=token:return False
            client,new,conflicts=save_card(row)
            seen=current.seen
            for key,values in {'organizations':[client.id],'created':[client.id] if new else [],'emails':row['emails'],'phones':[p['number'] for p in row['phones']]}.items():seen[key]=sorted(set(seen.get(key,[]))|set(values))
            current.seen=seen;previous=current.report
            current.report={**previous,'processed':previous.get('processed',0)+1,'organizations':len(seen['organizations']),'created':len(seen['created']),'updated':len(set(seen['organizations'])-set(seen['created'])),'emails':len(seen['emails']),'phones':len(seen['phones']),'issues':previous.get('issues',0)+len(row['issues']),'conflicts':previous.get('conflicts',0)+len(conflicts)}
            current.cursor+=1;current.attempts=0;current.error=''
            schedule(current);current.save()
            log(f'Gov.kz: {row["name"][:140]}. Сохранено email: {len(row["emails"])}, телефонов: {len({p["number"] for p in row["phones"]})}.',source='gov')
        return True
    except ContactsNotPublished:
        with transaction.atomic():
            current=GovCollection.objects.select_for_update().get(pk=1)
            if not current.enabled or current.lease!=token:return False
            current.report={**current.report,'without_contacts':current.report.get('without_contacts',0)+1}
            current.cursor+=1;current.attempts=0;current.error='';schedule(current);current.save()
            log(f'{state.urls[state.cursor]} — текстовые контакты не опубликованы; переход к следующей карточке.','info',source='gov')
        return True
    except Exception as exc:
        with transaction.atomic():
            current=GovCollection.objects.select_for_update().get(pk=1)
            if current.lease!=token or not current.enabled:return False
            current.attempts+=1;current.error=str(exc)[:300] if isinstance(exc,SourceError) else 'Ошибка обработки Gov.kz. Данные сохранены.'
            delay=max(60*2**min(current.attempts,8),getattr(exc,'retry_after',60))
            if current.attempts>=3:
                report=current.report;report['failed']=report.get('failed',0)+1;current.report=report
                current.cursor+=1;current.attempts=0;schedule(current)
            else:current.next_attempt_at=timezone.now()+timedelta(seconds=delay)
            # A rate-limit delay also applies when moving to the next card.
            current.next_attempt_at=max(current.next_attempt_at,timezone.now()+timedelta(seconds=delay));current.save()
            log(f'{state.urls[state.cursor]} — {current.error}'[:300],'warning',source='gov')
        return False
    finally:GovCollection.objects.filter(pk=1,lease=token).update(lease='',lease_until=None)

def schedule(state):
    done=state.cursor>=len(state.urls)
    state.completed_at=timezone.now() if done else None
    state.next_attempt_at=timezone.now()+timedelta(hours=6) if done else timezone.now()+timedelta(seconds=3)

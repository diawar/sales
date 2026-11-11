import hashlib
import json
import re
import uuid
from datetime import timedelta
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import KaspiCollection,KaspiMerchant,KaspiTask,CollectionLog,Client
from .kaspi_client import KaspiClient,KaspiFormat,KaspiBlocked,KaspiRetry,CATEGORIES,CATEGORY_MAP
from .kaspi_pilot import audience
from .profiles import apply_profile,attach_contacts,fact

INTERVAL=10

def log(message,level='info'):
    CollectionLog.objects.create(source='kaspi',level=level,message=message[:300])

def enqueue(state,kind,key,payload,priority):
    return KaspiTask.objects.get_or_create(cycle=state.cycle,kind=kind,key=key,
        defaults={'payload':payload,'priority':priority})[0]

def seed(state):
    for category in CATEGORIES:
        code=category['code']
        enqueue(state,'catalog',code+':0',{'code':code,'page':0},0)

@transaction.atomic
def control(action):
    state,_=KaspiCollection.objects.get_or_create(pk=1)
    state=KaspiCollection.objects.select_for_update().get(pk=1)
    if action=='start':
        state.enabled=True;state.error=''
        # A blocked task resumes from the same page, never from the beginning.
        KaspiTask.objects.filter(cycle=state.cycle,state='blocked').update(state='queued',error='')
        seed(state)
    elif action=='pause':state.enabled=False
    elif action=='retry':
        KaspiTask.objects.filter(cycle=state.cycle,state__in=['review','blocked']).update(state='queued',attempts=0,error='')
        state.error=''
    else:raise ValueError('Неизвестная команда Kaspi.')
    state.save()
    log('Kaspi: '+{'start':'сбор включён','pause':'сбор на паузе','retry':'повтор отложенных задач'}[action])

def save_merchant(merchant):
    root=audience('Бизнес',None);base=audience('Kaspi',root)
    url='https://kaspi.kz/shop/m/'+merchant.merchant_id+'/products/'
    client,new=Client.objects.get_or_create(identity_key='kaspi:merchant:'+merchant.merchant_id,defaults={'name':merchant.name})
    apply_profile(client,{'name':merchant.name,'city':merchant.city},'Kaspi',url)
    categories=[CATEGORY_MAP[c] for c in merchant.category_codes]
    categories=['Компьютеры']+(['Комплектующие'] if any(c!='desktops' for c in merchant.category_codes) else [])+categories
    attach_contacts(client,[],[p['raw'] for p in merchant.phones],[root,base]+[audience(c,base) for c in dict.fromkeys(categories)],'Kaspi',url)
    for key,value in {'kaspi_merchant_id':merchant.merchant_id,'addresses':merchant.addresses,
                      'kaspi_categories':categories,'kaspi_discovery':merchant.source_urls}.items():
        client.facts.filter(field=key,source='Kaspi',source_url=url,state='accepted').update(state='superseded')
        fact(client,key,value,'Kaspi',url,'accepted')
    merchant.client=client;merchant.save(update_fields=['client'])
    return new

def signature(values):return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()

def handle(state,task,result):
    p=task.payload
    if task.kind=='catalog':
        ids=[r['id'] for r in result]
        digest=signature(sorted(set(ids)))
        if ids and p.get('previous_signature')==digest:raise KaspiFormat('Kaspi повторил страницу каталога; очередь сохранена для проверки.')
        for row in result:
            enqueue(state,'offers',p['code']+':'+row['id']+':0',{'code':p['code'],'pid':row['id'],'url':row['url'],'page':0},20)
        if result:
            if p['page']>=9999:raise KaspiFormat('Достигнут защитный предел страниц каталога; требуется проверка.')
            enqueue(state,'catalog',p['code']+':'+str(p['page']+1),{**p,'page':p['page']+1,'previous_signature':digest},30)
    elif task.kind=='offers':
        ids=[r['id'] for r in result['rows']];digest=signature(sorted(set(ids)))
        if ids and p.get('previous_signature')==digest:raise KaspiFormat('Kaspi повторил страницу предложений; требуется проверка.')
        for row in result['rows']:
            merchant,_=KaspiMerchant.objects.get_or_create(merchant_id=row['id'],defaults={'name':row['name']})
            if p['code'] not in merchant.category_codes:merchant.category_codes.append(p['code'])
            if p['url'] not in merchant.source_urls and len(merchant.source_urls)<100:merchant.source_urls.append(p['url'])
            merchant.save()
            if merchant.client_id:save_merchant(merchant)
            enqueue(state,'details',row['id'],{'mid':row['id']},10)
        if (p['page']+1)*5<result['total']:
            if p['page']>=9999:raise KaspiFormat('Достигнут предел страниц предложений; требуется проверка.')
            enqueue(state,'offers',p['code']+':'+p['pid']+':'+str(p['page']+1),{**p,'page':p['page']+1,'previous_signature':digest},20)
    elif task.kind=='details':
        merchant=KaspiMerchant.objects.get(merchant_id=p['mid'])
        merchant.name=result['shop_name'];merchant.addresses=result['addresses']
        cities=[]
        for address in merchant.addresses:
            match=re.search(r'(?:^|[,;])\s*г\.\s*([^,;]+)',address,re.I)
            if match and match[1].strip() not in cities:cities.append(match[1].strip())
        merchant.city=cities[0] if len(cities)==1 else ''
        merchant.save();enqueue(state,'support',p['mid'],{'mid':p['mid']},10)
    elif task.kind=='support':
        merchant=KaspiMerchant.objects.get(merchant_id=p['mid']);merchant.phones=result;merchant.checked_at=timezone.now();merchant.save()
        new=save_merchant(merchant)
        key='created' if new else 'updated';state.report[key]=state.report.get(key,0)+1
        log(f'Kaspi: магазин {merchant.merchant_id}, контакты сохранены.')
    task.state='done';task.error='';task.save(update_fields=['state','error'])

def run_round(client=None):
    now=timezone.now();KaspiCollection.objects.get_or_create(pk=1)
    KaspiCollection.objects.filter(pk=1).update(heartbeat_at=now)
    token=uuid.uuid4().hex
    owned=KaspiCollection.objects.filter(pk=1,enabled=True).filter(Q(lease_until__isnull=True)|Q(lease_until__lte=now)).filter(
        Q(next_request_at__isnull=True)|Q(next_request_at__lte=now)).update(lease=token,lease_until=now+timedelta(minutes=2))
    if not owned:return False
    state=KaspiCollection.objects.get(pk=1)
    if state.completed_at:
        state.cycle+=1;state.completed_at=None;state.report={};state.save();seed(state)
    task=KaspiTask.objects.filter(cycle=state.cycle,state='queued',next_at__lte=now).order_by('priority','id').first()
    if not task:
        waiting=KaspiTask.objects.filter(cycle=state.cycle,state='queued').order_by('next_at').first()
        state.lease='';state.lease_until=None
        if waiting:state.next_request_at=max(now+timedelta(seconds=INTERVAL),waiting.next_at)
        else:
            state.completed_at=now;state.next_request_at=now+timedelta(hours=24)
            log('Kaspi: доступная очередь обработана; следующий цикл через 24 часа.')
        state.save();return False
    own=client is None;reader=client or KaspiClient()
    try:
        p=task.payload
        if task.kind=='catalog':result=reader.catalogue(p['code'],p['page'],state.city_code)
        elif task.kind=='offers':result=reader.offers(p['pid'],p['url'],p['page'],state.city_code)
        elif task.kind=='details':result=reader.details(p['mid'])
        elif task.kind=='support':result=reader.support(p['mid'],state.city_code)
        else:raise KaspiFormat('Неизвестная задача Kaspi.')
        with transaction.atomic():
            current=KaspiCollection.objects.select_for_update().get(pk=1)
            if current.lease!=token or not current.enabled:return False
            handle(current,task,result)
            current.report['requests']=current.report.get('requests',0)+1
            current.next_request_at=timezone.now()+timedelta(seconds=INTERVAL);current.error='';current.save()
        return True
    except (KaspiBlocked,KaspiFormat,KaspiRetry) as exc:
        with transaction.atomic():
            current=KaspiCollection.objects.select_for_update().get(pk=1)
            if current.lease!=token:return False
            task.refresh_from_db();task.attempts+=1;task.error=str(exc)[:300]
            current.error=task.error
            delay=max(INTERVAL,getattr(exc,'delay',30)*(2**min(task.attempts-1,3)))
            if isinstance(exc,(KaspiBlocked,KaspiFormat)):
                task.state='blocked';current.enabled=False
            elif task.attempts>=3:task.state='review'
            task.next_at=timezone.now()+timedelta(seconds=delay);task.save()
            current.next_request_at=task.next_at;current.save()
            log(task.error,'warning')
        return False
    finally:
        if own:reader.close()
        KaspiCollection.objects.filter(pk=1,lease=token).update(lease='',lease_until=None)

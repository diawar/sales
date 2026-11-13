"""Statistics of explicitly synthetic events only; no tracking endpoints or transport."""
import hashlib
import json
import uuid
from datetime import datetime, time, timedelta, timezone as datetime_timezone
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email, URLValidator
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.core.paginator import Paginator
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_date
from .models import EmailCampaign, CampaignTestEvent

KINDS={'sent':'Передано на отправку','delivered':'Доставка','open':'Открытие','click':'Переход','unsubscribe':'Отписка','error':'Ошибка'}


def campaign_scope(value, allow_all=True):
    if value=='demo': return None, 'Демонстрационный набор'
    if allow_all and value in ('',None,'all'): return 'all','Все рассылки'
    if isinstance(value,bool) or not isinstance(value,(int,str)) or not str(value).isascii() or not str(value).isdigit():
        raise ValueError('Выберите рассылку или демонстрационный набор.')
    row=EmailCampaign.objects.filter(pk=int(value)).first()
    if not row: raise ValueError('Рассылка не найдена.')
    return row.pk,row.name


def fingerprint(values):
    return hashlib.sha256(json.dumps(values,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def event_values(data):
    if data.get('source','test')!='test': raise ValueError('Доступны только тестовые события.')
    campaign_id,_=campaign_scope(data.get('campaign_id'),False)
    try: event_id=uuid.UUID(str(data.get('event_id','')))
    except (ValueError,TypeError,AttributeError): raise ValueError('Нужен UUID события.')
    values={'campaign_id':campaign_id}
    for field,limit in [('recipient',254),('kind',20),('link',2000),('error_type',16),('detail',500)]:
        value=data.get(field,'')
        if not isinstance(value,str) or len(value)>limit: raise ValueError('Проверьте поля события.')
        values[field]=value.strip()
    values['recipient']=values['recipient'].casefold()
    try: validate_email(values['recipient'])
    except ValidationError: raise ValueError('Укажите корректный email тестового получателя.')
    if values['kind'] not in KINDS: raise ValueError('Выберите тип события.')
    if values['kind']=='click':
        try: URLValidator(schemes=['https','http'])(values['link'])
        except ValidationError: raise ValueError('Для перехода нужен полный HTTP(S)-адрес.')
    elif values['link']: raise ValueError('Ссылка указывается только для перехода.')
    if values['kind']=='error':
        if values['error_type'] not in ('temporary','permanent'): raise ValueError('Выберите временную или окончательную ошибку.')
    elif values['error_type']: raise ValueError('Вид ошибки допустим только для события «Ошибка».')
    date=data.get('occurred_at')
    if date is None or date=='': occurred=timezone.now(); normalized_date=None
    else:
        if not isinstance(date,str) or len(date)>40: raise ValueError('Проверьте время события.')
        try: occurred=parse_datetime(date)
        except ValueError: occurred=None
        if occurred is None: raise ValueError('Проверьте время события.')
        if timezone.is_naive(occurred): occurred=timezone.make_aware(occurred)
        normalized_date=occurred.astimezone(datetime_timezone.utc).isoformat()
    if occurred.year<1970 or occurred>timezone.now()+timedelta(minutes=5): raise ValueError('Время события должно быть в прошлом или текущем времени.')
    digest=fingerprint({**values,'occurred_at':normalized_date})
    return event_id,{**values,'occurred_at':occurred,'fingerprint':digest}


def add_event(data):
    event_id,values=event_values(data)
    with transaction.atomic():
        row,created=CampaignTestEvent.objects.get_or_create(event_id=event_id,defaults=values)
        if row.fingerprint!=values['fingerprint']: raise ValueError('Этот идентификатор уже использован для другого события.')
    return row,created


def demo(value):
    campaign_id,_=campaign_scope(value,False)
    key='demo' if campaign_id is None else str(campaign_id)
    def event_id(i,kind,sequence=0): return uuid.uuid5(uuid.NAMESPACE_URL,f'sales-test-statistics:{key}:{i}:{kind}:{sequence}')
    with transaction.atomic():
        # Demo population is idempotent even when the button is used concurrently.
        first=CampaignTestEvent.objects.filter(event_id=event_id(0,'sent')).first()
        anchor=first.occurred_at if first else timezone.now()-timedelta(days=6,hours=1)
        rows=[]
        def append(i,kind,sequence=0,**extra):
            values={'campaign_id':campaign_id,'recipient':f'demo-{key}-{i:03}@example.invalid','kind':kind,
                    'occurred_at':anchor+timedelta(days=i%7,minutes=i//7,seconds=sequence+{'sent':0,'error':5,'delivered':10,'open':20,'click':30,'unsubscribe':40}[kind]),
                    'link':'','error_type':'','detail':'Синтетическое событие; письмо не отправлялось.'}
            values.update(extra)
            digest=fingerprint({**values,'occurred_at':values['occurred_at'].isoformat()})
            rows.append(CampaignTestEvent(event_id=event_id(i,kind,sequence),fingerprint=digest,**values))
        for i in range(100):
            append(i,'sent');group=i%10
            if group==0: append(i,'error',error_type='permanent');continue
            if group==1: append(i,'error',error_type='temporary')
            if group==2: continue
            append(i,'delivered')
            if group in (4,5,6,8,9): append(i,'open')
            if group in (5,8): append(i,'open',1)
            if group in (5,7,8): append(i,'click',link='https://example.invalid/offers' if group!=7 else 'https://example.invalid/catalog')
            if group==8: append(i,'click',1,link='https://example.invalid/offers')
            if group in (6,8): append(i,'unsubscribe')
        existing={r.event_id:r.campaign_id for r in CampaignTestEvent.objects.filter(event_id__in=[r.event_id for r in rows])}
        if any(cid!=campaign_id for cid in existing.values()): raise ValueError('Идентификатор демонстрации занят другим набором.')
        new=[row for row in rows if row.event_id not in existing]
        CampaignTestEvent.objects.bulk_create(new,batch_size=200,ignore_conflicts=True)
    return len(new)


def base_events(params):
    scope,label=campaign_scope(params.get('campaign_id','all'))
    rows=CampaignTestEvent.objects.all()
    rows=rows.exclude(campaign=None) if scope=='all' else rows.filter(campaign_id=scope)
    start=end=None
    for key in ('from','to'):
        value=params.get(key,'')
        if not isinstance(value,str) or len(value)>10: raise ValueError('Проверьте период.')
        try: date=parse_date(value) if value else None
        except ValueError: date=None
        if value and (not date or not 1970<=date.year<9999): raise ValueError('Проверьте период (1970–9998).')
        if key=='from': start=date
        else: end=date
    if start and end and start>end: raise ValueError('Начало периода должно быть не позже окончания.')
    if start: rows=rows.filter(occurred_at__gte=timezone.make_aware(datetime.combine(start,time.min)))
    if end:
        if end.year>=9999: raise ValueError('Проверьте окончание периода.')
        rows=rows.filter(occurred_at__lt=timezone.make_aware(datetime.combine(end+timedelta(days=1),time.min)))
    return rows,label


def percentage(numerator,denominator): return round(100*numerator/denominator,2) if denominator else None


def statistics(params):
    rows,label=base_events(params)
    totals=rows.aggregate(**{kind:Count('pk',filter=Q(kind=kind)) for kind in KINDS})
    sets={kind:set() for kind in KINDS}
    for recipient,kind in rows.values_list('recipient','kind').distinct(): sets[kind].add(recipient)
    unique={kind:len(recipients) for kind,recipients in sets.items()}
    delivered=sets['delivered'];sent=sets['sent']
    rates={'delivery':percentage(len(delivered&sent),len(sent)),
           'open':percentage(len(sets['open']&delivered),len(delivered)),
           'click':percentage(len(sets['click']&delivered),len(delivered)),
           'unsubscribe':percentage(len(sets['unsubscribe']&delivered),len(delivered)),
           'error':percentage(len(sets['error']&sent),len(sent))}
    error_types=list(rows.filter(kind='error').values('error_type').annotate(total=Count('pk'),unique=Count('recipient',distinct=True)).order_by('error_type'))
    days=list(rows.annotate(day=TruncDate('occurred_at')).values('day').annotate(**{kind:Count('recipient',filter=Q(kind=kind),distinct=True) for kind in KINDS}).order_by('-day')[:91])
    days_limited=len(days)>90
    days=list(reversed(days[:90]))
    links=list(rows.filter(kind='click').values('link').annotate(total=Count('pk'),unique=Count('recipient',distinct=True)).order_by('-total','link')[:20])
    history=rows.select_related('campaign')
    kind=params.get('kind','')
    if kind and kind not in KINDS: raise ValueError('Некорректный тип события.')
    if kind: history=history.filter(kind=kind)
    query=params.get('query','')
    if not isinstance(query,str) or len(query)>254: raise ValueError('Слишком длинный поиск email.')
    if query.strip(): history=history.filter(recipient__icontains=query.strip())
    direction=params.get('direction','desc')
    if direction not in ('asc','desc'): raise ValueError('Некорректная сортировка.')
    history=history.order_by(('' if direction=='asc' else '-')+'occurred_at',('' if direction=='asc' else '-')+'id')
    try: page=int(params.get('page',1))
    except (TypeError,ValueError): raise ValueError('Некорректная страница.')
    if not 1<=page<=2147483647: raise ValueError('Некорректная страница.')
    paginator=Paginator(history,25);current=paginator.page(min(page,paginator.num_pages))
    return {'source':'test','label':label,'timezone':settings.TIME_ZONE,'totals':totals,'unique':unique,'rates':rates,
            'errors':error_types,'days':days,'days_limited':days_limited,'links':links,
            'history':[{'id':str(row.event_id),'campaign':row.campaign.name if row.campaign_id else 'Демонстрационный набор',
                        'recipient':row.recipient,'kind':row.kind,'occurred_at':row.occurred_at,'link':row.link,
                        'error_type':row.error_type,'detail':row.detail} for row in current],
            'pagination':{'page':current.number,'pages':paginator.num_pages,'total':paginator.count,'has_next':current.has_next(),'has_previous':current.has_previous()}}

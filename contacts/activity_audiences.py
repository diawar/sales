"""Virtual activity audiences. Only synthetic events are read, never client profiles."""
import uuid
from collections import Counter
from datetime import timedelta
from django.core.paginator import Paginator
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import ActivityTestEvent,CampaignTestEvent
from .campaign_statistics import campaign_scope

LABELS={'active':'Активные','passive':'Пассивные','insufficient':'Мало данных','excluded':'Исключены'}


def integer(params,key,default,minimum,maximum):
    try:value=int(params.get(key,default))
    except (TypeError,ValueError):raise ValueError('Проверьте числовые параметры правил.')
    if not minimum<=value<=maximum:raise ValueError(f'{dict(lookback_days="Период активности",min_delivered="Минимум писем",quiet_days="Ожидание после доставки",page="Страница").get(key,key)}: от {minimum} до {maximum}.')
    return value


def rules(params):
    result={'lookback_days':integer(params,'lookback_days',90,1,730),
            'min_delivered':integer(params,'min_delivered',3,1,20),
            'quiet_days':integer(params,'quiet_days',7,0,180)}
    if result['quiet_days']>result['lookback_days']:raise ValueError('Ожидание после доставки не должно превышать период активности.')
    value=params.get('as_of','')
    if value:
        if not isinstance(value,str) or len(value)>40:raise ValueError('Проверьте дату расчёта.')
        try:moment=parse_datetime(value)
        except ValueError:moment=None
        if not moment or not 1970<=moment.year<9999:raise ValueError('Проверьте дату расчёта.')
        if timezone.is_naive(moment):moment=timezone.make_aware(moment)
    else:moment=timezone.now()
    return result,moment


def event_rows(params,moment):
    source=params.get('source','activity_demo')
    if source=='activity_demo':
        rows=ActivityTestEvent.objects.filter(occurred_at__lte=moment).order_by('pk').values('recipient','kind','message_key','error_type','occurred_at')
        return rows.iterator(chunk_size=1000),'Демонстрация активности'
    if source!='statistics':raise ValueError('Выберите источник тестовых событий.')
    scope,label=campaign_scope(params.get('campaign_id','demo'))
    rows=CampaignTestEvent.objects.filter(occurred_at__lte=moment)
    rows=rows.exclude(campaign=None) if scope=='all' else rows.filter(campaign_id=scope)
    def iterator():
        for row in rows.order_by('pk').values('recipient','kind','campaign_id','error_type','occurred_at').iterator(chunk_size=1000):
            row['message_key']='statistics:'+str(row.pop('campaign_id') or 'demo')
            yield row
    return iterator(),'Тестовая статистика: '+label


def classification(params, *, test_events=None):
    settings,moment=rules(params);cutoff=moment-timedelta(days=settings['lookback_days']);quiet=moment-timedelta(days=settings['quiet_days'])
    events,label=event_rows(params,moment) if test_events is None else (test_events,'Сквозная репетиция')
    people={};event_count=0
    for event in events:
        event_count+=1;email=event['recipient'].strip().casefold();date=event['occurred_at'];kind=event['kind']
        row=people.setdefault(email,{'email':email,'delivery_dates':{},'opens':0,'clicks':0,'last_delivery':None,'last_click':None,'last_seen':date,'optout':False,'hard_error':False})
        row['last_seen']=max(row['last_seen'],date)
        # Opt-outs/errors do not silently disappear when the activity window expires.
        if kind=='unsubscribe':row['optout']=True
        if kind=='error' and event['error_type']=='permanent':row['hard_error']=True
        if kind=='delivered':
            key=event['message_key'] or 'unknown-message'
            previous=row['delivery_dates'].get(key)
            row['delivery_dates'][key]=min(previous,date) if previous else date
        if date<cutoff:continue
        if kind=='open':row['opens']+=1
        elif kind=='click':
            row['clicks']+=1;row['last_click']=max(row['last_click'],date) if row['last_click'] else date
    result=[]
    for email,row in people.items():
        delivery_dates=[date for date in row.pop('delivery_dates').values() if date>=cutoff]
        delivered=len(delivery_dates);row['delivered']=delivered
        row['last_delivery']=max(delivery_dates) if delivery_dates else None
        if row['optout'] or row['hard_error']:
            state='excluded';reason='Тестовая отписка.' if row['optout'] else 'Окончательная ошибка в тестовых событиях.'
        elif row['clicks']:
            state='active';reason=f'Есть переходы за {settings["lookback_days"]} дней.'
        elif delivered>=settings['min_delivered'] and row['last_delivery']<=quiet:
            state='passive';reason=f'Нет переходов; доставлено разных писем: {delivered}; выдержан срок {settings["quiet_days"]} дней.'
        else:
            state='insufficient';reason='Недостаточно разных доставленных писем.' if delivered<settings['min_delivered'] else 'Последнее письмо доставлено недавно: период ожидания ещё не завершён.'
        row.update(state=state,label=LABELS[state],reason=reason);row.pop('optout');row.pop('hard_error');result.append(row)
    counts=Counter(row['state'] for row in result)
    return settings,moment,result,counts,len(people),event_count,label


def report(params):
    settings,moment,result,counts,total,event_count,label=classification(params)
    state=params.get('state','');query=params.get('query','').strip().casefold();sort=params.get('sort','email');direction=params.get('direction','asc')
    if state and state not in LABELS:raise ValueError('Некорректная аудитория активности.')
    if len(query)>254:raise ValueError('Слишком длинный поиск email.')
    if sort not in ('email','delivered','opens','clicks','last_delivery','last_click','last_seen','state'):raise ValueError('Некорректная сортировка.')
    if direction not in ('asc','desc'):raise ValueError('Некорректное направление сортировки.')
    result=[r for r in result if (not state or r['state']==state) and (not query or query in r['email'])]
    # Missing dates always appear after known dates, in both directions.
    known=[r for r in result if r[sort] is not None];missing=[r for r in result if r[sort] is None]
    known.sort(key=lambda r:(r[sort],r['email']),reverse=direction=='desc');missing.sort(key=lambda r:r['email']);result=known+missing
    page=integer(params,'page',1,1,2147483647);paginator=Paginator(result,25);current=paginator.page(min(page,paginator.num_pages))
    return {'test_only':True,'can_send':False,'source':params.get('source','activity_demo'),'label':label,
            'calculated_at':timezone.now(),'as_of':moment,'timezone':timezone.get_current_timezone_name(),'rules':settings,'total':total,'events':event_count,
            'counts':{key:counts[key] for key in LABELS},'recipients':list(current),
            'pagination':{'page':current.number,'pages':paginator.num_pages,'total':paginator.count,'has_next':current.has_next(),'has_previous':current.has_previous()},
            'note':'Это виртуальные группы по тестовым событиям. Реальные клиенты, аудитории, согласия, запреты и очереди не изменяются. Открытия не подтверждают прочтение и не заменяют переходы.'}


def populate_demo():
    def event_id(key):return uuid.uuid5(uuid.NAMESPACE_URL,'sales-activity-demo:'+key)
    first=ActivityTestEvent.objects.filter(event_id=event_id('active:delivered:0')).first()
    anchor=first.occurred_at+timedelta(days=10) if first else timezone.now()
    rows=[]
    def add(person,kind,days,message='',error='',sequence=0):
        key=f'{person}:{kind}:{sequence}'
        rows.append(ActivityTestEvent(event_id=event_id(key),recipient=f'activity-{person}@example.invalid',kind=kind,
            message_key=message,error_type=error,occurred_at=anchor-timedelta(days=days)))
    def deliveries(person,days):
        for i,day in enumerate(days):add(person,'delivered',day,'message-'+str(i),sequence=i)
    add('active','delivered',10,'message-0');add('active','click',1)
    deliveries('passive',[30,20,10])
    deliveries('reader',[30,20,10]);add('reader','open',1)
    deliveries('new',[1]);deliveries('waiting',[30,20,2])
    add('optout','click',1);add('optout','unsubscribe',.5)
    add('hard-error','error',1,error='permanent')
    deliveries('soft-error',[30,20,10]);add('soft-error','error',1,error='temporary')
    add('opens-only','open',1)
    for i in range(3):add('duplicate','delivered',20,'same-message',sequence=i)
    deliveries('expired-click',[30,20,10]);add('expired-click','click',100)
    with transaction.atomic():
        existing=set(ActivityTestEvent.objects.filter(event_id__in=[r.event_id for r in rows]).values_list('event_id',flat=True))
        new=[row for row in rows if row.event_id not in existing]
        ActivityTestEvent.objects.bulk_create(new,batch_size=100,ignore_conflicts=True)
    return len(new)


def add_test_event(data):
    from django.core.validators import validate_email
    from django.core.exceptions import ValidationError
    if data.get('source','test')!='test' or data.get('test_only',True) is not True:raise ValueError('Доступны только тестовые события.')
    try:event_id=uuid.UUID(str(data.get('event_id','')))
    except (ValueError,TypeError,AttributeError):raise ValueError('Нужен UUID события.')
    values={}
    for key,limit in [('recipient',254),('kind',20),('message_key',120),('error_type',16)]:
        value=data.get(key,'')
        if not isinstance(value,str) or len(value)>limit:raise ValueError('Проверьте поля тестового события.')
        values[key]=value.strip()
    values['recipient']=values['recipient'].casefold()
    try:validate_email(values['recipient'])
    except ValidationError:raise ValueError('Укажите корректный тестовый email.')
    if values['kind'] not in ('sent','delivered','open','click','unsubscribe','error'):raise ValueError('Выберите событие.')
    if values['kind']=='delivered' and not values['message_key']:raise ValueError('Для доставки нужен идентификатор письма.')
    if values['kind']=='error':
        if values['error_type'] not in ('temporary','permanent'):raise ValueError('Выберите вид ошибки.')
    elif values['error_type']:raise ValueError('Вид ошибки указывается только для события ошибки.')
    value=data.get('occurred_at','')
    explicit=bool(value)
    if value:
        if not isinstance(value,str) or len(value)>40:raise ValueError('Проверьте дату события.')
        try:date=parse_datetime(value)
        except ValueError:date=None
        if not date:raise ValueError('Проверьте дату события.')
        if timezone.is_naive(date):date=timezone.make_aware(date)
    else:date=timezone.now()
    if date.year<1970 or date>timezone.now()+timedelta(minutes=5):raise ValueError('Событие должно относиться к прошлому или текущему времени.')
    with transaction.atomic():
        row,created=ActivityTestEvent.objects.get_or_create(event_id=event_id,defaults={**values,'occurred_at':date})
        if any(getattr(row,key)!=value for key,value in values.items()) or explicit and row.occurred_at!=date:
            raise ValueError('Этот идентификатор уже занят другим тестовым событием.')
    return row,created

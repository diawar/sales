"""Shared simulation-only frequency gate. No production contacts or transport."""
from datetime import timedelta
from django.core.paginator import Paginator
from django.db import transaction, connection
from django.db.models import Max, F
from django.utils import timezone
from .models import (TestFrequencyPolicy, TestFrequencyChange, AutomationSimulationClock,
    AutomationSimulationAction, CampaignRehearsal, RehearsalLetter)
from . import action_audit as audit

FIELDS=('enabled','min_interval_minutes','max_24h','max_7d')
DEFAULT={'enabled':True,'min_interval_minutes':1440,'max_24h':1,'max_7d':3}


def latest_clock():
    values=[timezone.now()]
    for model,field in ((TestFrequencyPolicy,'clock_at'),(AutomationSimulationClock,'clock_at'),(CampaignRehearsal,'clock_at'),
        (AutomationSimulationAction,'occurred_at'),(RehearsalLetter,'prepared_at')):
        value=model.objects.aggregate(value=Max(field))['value']
        if value:values.append(value)
    return max(values)


def lock_gate():
    # Global gate must be acquired before the simulator clock or a rehearsal row.
    if connection.vendor=='sqlite':
        # SQLite ignores SELECT FOR UPDATE. Acquire its write lock before reading the shared history.
        exists=TestFrequencyPolicy.objects.filter(pk=1).update(revision=F('revision'))
    else:exists=TestFrequencyPolicy.objects.filter(pk=1).exists()
    if not exists:
        TestFrequencyPolicy.objects.get_or_create(pk=1,defaults={'clock_at':latest_clock()})
    return TestFrequencyPolicy.objects.select_for_update().get(pk=1)


def sync_clock(row,gate):
    if gate.enabled and row.clock_at!=gate.clock_at:
        row.clock_at=gate.clock_at;row.save(update_fields=['clock_at'])


def advance_gate(moment):
    gate=lock_gate()
    if gate.enabled:
        if moment<gate.clock_at:raise ValueError('Общие тестовые часы нельзя переводить назад.')
        gate.clock_at=moment;gate.save(update_fields=['clock_at'])


def history(email=None,after=None,before=None):
    """Each prepared letter or simulated action is one logical contact, not a retry."""
    actions=AutomationSimulationAction.objects.all()
    letters=RehearsalLetter.objects.filter(message__is_test=True)
    if email:
        email=email.strip().casefold()
        # Both writers persist the classifier's strip/casefold key; exact lookups use the email/time indexes.
        actions=actions.filter(recipient=email);letters=letters.filter(recipient=email)
    if after:
        actions=actions.filter(occurred_at__gt=after);letters=letters.filter(prepared_at__gt=after)
    if before:
        actions=actions.filter(occurred_at__lte=before);letters=letters.filter(prepared_at__lte=before)
    rows=[]
    for r in actions.values('pk','recipient','occurred_at','campaign_id','member__run_id','member__run__scenario_id'):
        rows.append({'key':'scenario:'+str(r['pk']),'source':'scenario','id':r['pk'],'email':r['recipient'].strip().casefold(),
            'at':r['occurred_at'],'campaign_id':r['campaign_id'],'scenario_id':r['member__run__scenario_id'],'run_id':r['member__run_id']})
    for r in letters.values('pk','recipient','prepared_at','message__campaign_id','rehearsal_id','rehearsal__scenario_id'):
        rows.append({'key':'rehearsal:'+str(r['pk']),'source':'rehearsal','id':r['pk'],'email':r['recipient'].strip().casefold(),
            'at':r['prepared_at'],'campaign_id':r['message__campaign_id'],'scenario_id':r['rehearsal__scenario_id'],'run_id':r['rehearsal_id']})
    return sorted(rows,key=lambda r:(r['at'],r['source'],r['id']))


def plan(email,now,gate=None):
    gate=gate or TestFrequencyPolicy.objects.filter(pk=1).first()
    if not gate or not gate.enabled:return {'enabled':False,'allowed_at':now,'reasons':[],'count_24h':0,'count_7d':0,'last':None}
    records=history(email,after=now-timedelta(days=7),before=now)
    dates=[r['at'] for r in records];result=now;reasons=[]
    if dates:
        due=dates[-1]+timedelta(minutes=gate.min_interval_minutes)
        if due>now:result=max(result,due);reasons.append('Минимальный интервал между письмами')
    daily=[d for d in dates if d>now-timedelta(days=1)]
    for dates_in_window,days,limit,label in ((daily,1,gate.max_24h,'Скользящий лимит за 24 часа'),(dates,7,gate.max_7d,'Скользящий лимит за 7 дней')):
        if len(dates_in_window)>=limit:
            result=max(result,dates_in_window[len(dates_in_window)-limit]+timedelta(days=days));reasons.append(label)
    return {'enabled':True,'allowed_at':result,'reasons':reasons,'count_24h':len(daily),'count_7d':len(dates),
        'last':records[-1] if records else None,'revision':gate.revision}


def explanation(decision,effective_at=None):
    last=decision['last'];origin=''
    if last:origin=f' Последний контакт: {"репетиция" if last["source"]=="rehearsal" else "сценарий"} #{last["run_id"]}, рассылка #{last["campaign_id"]}.'
    due=max(decision['allowed_at'],effective_at or decision['allowed_at'])
    local=' Дополнительно учтены более строгие местные ограничения.' if due>decision['allowed_at'] else ''
    return ('Общая частота: '+'; '.join(decision['reasons'])+
        f'. За 24 ч: {decision["count_24h"]}, за 7 дней: {decision["count_7d"]}. Ближайший срок: {timezone.localtime(due).isoformat()}. Версия {decision["revision"]}.'+origin+local)


@transaction.atomic
def configure(data):
    if data.get('mode')!='simulation' or data.get('test_only') is not True or data.get('send',False) is not False:
        raise ValueError('Доступен только тестовый режим без отправки.')
    gate=lock_gate()
    if type(data.get('revision')) is not int or data['revision']!=gate.revision:raise ValueError('Настройки изменились. Обновите страницу.')
    config=data.get('policy')
    if not isinstance(config,dict) or set(config)!=set(FIELDS) or type(config['enabled']) is not bool:raise ValueError('Проверьте общую политику частоты.')
    for key,high in (('min_interval_minutes',10080),('max_24h',100),('max_7d',500)):
        if type(config[key]) is not int or not 1<=config[key]<=high:raise ValueError(f'{key}: целое число от 1 до {high}.')
    if config['max_24h']>config['max_7d']:raise ValueError('Недельный максимум должен быть не меньше суточного.')
    before={k:getattr(gate,k) for k in (*FIELDS,'revision')}
    if config['enabled']:gate.clock_at=latest_clock()
    for key,value in config.items():setattr(gate,key,value)
    gate.revision+=1;gate.save()
    TestFrequencyChange.objects.create(revision=gate.revision,snapshot=config,clock_at=gate.clock_at)
    audit.record('settings_changed','frequency',gate.pk,'Общий контроль частоты',before=before,
        after={**config,'revision':gate.revision},summary='Общие тестовые ограничения частоты изменены.')
    return gate


def report(params):
    gate=TestFrequencyPolicy.objects.filter(pk=1).first()
    query=params.get('query','').strip().casefold();source=params.get('source','');direction=params.get('direction','desc')
    if len(query)>254 or source not in ('','rehearsal','scenario') or direction not in ('asc','desc'):raise ValueError('Проверьте фильтры общей частоты.')
    try:page=int(params.get('page',1))
    except (ValueError,TypeError):raise ValueError('Проверьте страницу журнала.') from None
    if page<1:raise ValueError('Страница должна быть больше нуля.')
    moment=gate.clock_at if gate and gate.enabled else latest_clock()
    rows=history(before=moment)
    selected=[r for r in rows if query in r['email'] and (not source or r['source']==source)]
    if direction=='desc':selected.reverse()
    pages=Paginator(selected,25);current=pages.page(min(page,pages.num_pages))
    decision=plan(query,moment,gate) if query and '@' in query else None
    return {'mode':'simulation','can_send':False,'policy':{k:getattr(gate,k) for k in FIELDS} if gate else DEFAULT,
        'revision':gate.revision if gate else 0,'clock_at':moment,'total':len(rows),'emails':len({r['email'] for r in rows}),
        'decision':decision,'history':list(current.object_list),'changes':list(TestFrequencyChange.objects.order_by('-revision')[:20].values('revision','snapshot','clock_at')),
        'pagination':{'page':current.number,'pages':pages.num_pages,'total':pages.count}}

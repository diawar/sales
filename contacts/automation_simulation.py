"""Persistent scenario simulator. No mail, SMTP, network or real audience writes."""
from collections import Counter
from datetime import timedelta
from django.core.paginator import Paginator
from django.db import transaction
from django.utils import timezone
from .activity_audiences import classification
from .campaign_statistics import campaign_scope
from .campaign_mail_check import snapshots
from . import test_frequency as frequency
from . import action_audit as audit
from .models import (EmailCampaign, AutomationScenario, AutomationSimulationClock,
    AutomationSimulationRun, AutomationSimulationMember, AutomationSimulationAction, AutomationSimulationLog)

KINDS={'started':'Запущен','entered':'Вход','not_eligible':'Не вошёл по условиям','simulated':'Шаг имитирован','frequency':'Ограничение частоты',
       'cooldown':'Ожидание повторного входа','excluded':'Исключён','exited':'Выход из условий',
       'paused':'Пауза','resumed':'Продолжен','cancelled':'Отменён'}


def integer(data,key,default,low,high):
    value=data.get(key,default)
    if type(value) is not int or not low<=value<=high:raise ValueError(f'{key}: целое число от {low} до {high}.')
    return value


def simulation_only(data):
    if data.get('mode')!='simulation' or data.get('test_only') is not True or data.get('send',False) is not False:
        raise ValueError('Доступна только имитация: mode=simulation, test_only=true. Отправка запрещена.')


def configuration(data):
    if not isinstance(data,dict):raise ValueError('Проверьте настройки сценария.')
    target=data.get('target','active');source=data.get('source','activity_demo');opens=data.get('opens','any')
    if target not in ('active','passive') or source not in ('activity_demo','statistics') or opens not in ('any','none','some'):
        raise ValueError('Выберите аудиторию, тестовый источник и условие открытий.')
    scope='demo'
    if source=='statistics':
        scope,_=campaign_scope(data.get('source_campaign_id','demo'))
        scope='demo' if scope is None else scope
    rules={'lookback_days':integer(data,'lookback_days',90,1,730),
           'min_delivered':integer(data,'min_delivered',3,1,20),'quiet_days':integer(data,'quiet_days',7,0,180)}
    if rules['quiet_days']>rules['lookback_days']:raise ValueError('Ожидание после доставки не должно превышать окно активности.')
    limits={'min_interval_minutes':integer(data,'min_interval_minutes',1440,1,10080),
            'max_24h':integer(data,'max_24h',1,1,10),'max_7d':integer(data,'max_7d',3,1,30),
            'cooldown_days':integer(data,'cooldown_days',7,1,365)}
    if limits['max_24h']>limits['max_7d']:raise ValueError('Недельный максимум должен быть не меньше суточного.')
    steps=data.get('steps')
    if not isinstance(steps,list) or not 1<=len(steps)<=5:raise ValueError('Нужно от 1 до 5 шагов сценария.')
    values=[]
    for step in steps:
        if not isinstance(step,dict):raise ValueError('Проверьте шаги сценария.')
        cid=integer(step,'campaign_id',None,1,2147483647)
        if not EmailCampaign.objects.filter(pk=cid).exists():raise ValueError('Выберите существующую рассылку для каждого шага.')
        values.append({'campaign_id':cid,'delay_minutes':integer(step,'delay_minutes',0,0,43200)})
    return {'target':target,'source':source,'source_campaign_id':scope,'opens':opens,**rules,**limits,'steps':values}


def lock_clock():
    gate=frequency.lock_gate()
    row,_=AutomationSimulationClock.objects.get_or_create(pk=1)
    row=AutomationSimulationClock.objects.select_for_update().get(pk=row.pk)
    frequency.sync_clock(row,gate)
    return row


def log(run,kind,detail,now,recipient='',audit_before=None):
    AutomationSimulationLog.objects.create(run=run,recipient=recipient,kind=kind,detail=detail[:500],occurred_at=now)
    audit.run_event(run,kind,detail,test_at=now,before=audit_before)


@transaction.atomic
def save(data,scenario_id=None):
    simulation_only(data)
    name=data.get('name','')
    if not isinstance(name,str) or not name.strip() or len(name)>160:raise ValueError('Название сценария: от 1 до 160 символов.')
    config=configuration(data.get('config'))
    lock_clock()
    if scenario_id is not None:
        row=AutomationScenario.objects.select_for_update().filter(pk=scenario_id).first()
        if not row:raise ValueError('Сценарий не найден.')
        if type(data.get('revision')) is not int or data['revision']!=row.revision:raise ValueError('Сценарий изменился. Откройте актуальную версию.')
        before=audit.scenario_snapshot(row)
        row.revision+=1
    else:row=AutomationScenario();before={}
    row.name=name.strip();row.config=config;row.save()
    audit.record('updated' if scenario_id else 'created','scenario',row.pk,row.name,
        campaigns=audit.scenario_campaigns(config)+audit.scenario_campaigns(before.get('config',{})),before=before,after=audit.scenario_snapshot(row))
    return row


def audience(config,now):
    params={k:config[k] for k in ('source','lookback_days','min_delivered','quiet_days')}
    params.update(as_of=now.isoformat(),campaign_id=config['source_campaign_id'])
    _,_,rows,_,total,event_count,_=classification(params)
    if total>1000 or event_count>10000:raise ValueError('Для одной имитации допустимо до 1000 получателей и 10 000 тестовых событий. Сузьте источник.')
    return {r['email']:r for r in rows}


def qualifies(person,config):
    if not person or person['state']!=config['target']:return False
    return config['opens']=='any' or (person['opens']==0 if config['opens']=='none' else person['opens']>0)


@transaction.atomic
def start(scenario_id,revision):
    clock=lock_clock();now=clock.clock_at
    row=AutomationScenario.objects.get(pk=scenario_id)
    if type(revision) is not int or row.revision!=revision:raise ValueError('Сценарий изменился. Обновите список.')
    if row.runs.filter(state__in=('running','paused')).exists():raise ValueError('У сценария уже есть незавершённая имитация. Продолжите или отмените её.')
    if AutomationSimulationRun.objects.filter(state__in=('running','paused')).count()>=10:raise ValueError('Допустимо до 10 одновременно открытых имитаций.')
    config=configuration(row.config);frozen=[]
    for step in config['steps']:
        campaign=EmailCampaign.objects.select_related('dispatch_queue').get(pk=step['campaign_id'])
        from .campaign_approval import require_approved
        approval_version=require_approved(campaign,recipients=True)
        template,sender,version,basis=snapshots(campaign)
        frozen.append({**step,'approval_version':approval_version,'campaign_name':campaign.name,'version':version,'basis':basis,'template':template,'sender':sender})
    audience(config,now)
    run=AutomationSimulationRun.objects.create(scenario=row,started_at=now,
        snapshot={'name':row.name,'revision':row.revision,'config':config,'steps':frozen,'mode':'simulation'})
    log(run,'started','Имитация запущена. Письма не отправляются; версии шагов зафиксированы.',now)
    process(clock)
    return run


def frequency_at(email,config,now):
    history=AutomationSimulationAction.objects.filter(recipient=email,occurred_at__lte=now)
    result=now
    last=history.order_by('-occurred_at').first()
    if last:result=max(result,last.occurred_at+timedelta(minutes=config['min_interval_minutes']))
    for days,limit in ((1,config['max_24h']),(7,config['max_7d'])):
        dates=list(history.filter(occurred_at__gt=now-timedelta(days=days)).order_by('occurred_at').values_list('occurred_at',flat=True))
        if len(dates)>=limit:result=max(result,dates[len(dates)-limit]+timedelta(days=days))
    return result


def process(clock):
    """Evaluate at the current shared clock. Missed times are not replayed in bulk."""
    now=clock.clock_at;budget=500;performed=0
    for run in AutomationSimulationRun.objects.filter(state='running').order_by('pk'):
        from .campaign_approval import require_steps
        try:require_steps(run.snapshot)
        except ValueError as exc:
            run.state='paused';run.save(update_fields=['state'])
            log(run,'paused','Остановка согласования: '+str(exc),now)
            continue
        config=run.snapshot['config'];people=audience(config,now);steps=run.snapshot['steps']
        members={m.recipient:m for m in run.members.all()}
        skips=set(run.logs.filter(kind='not_eligible').values_list('recipient',flat=True))
        cooldowns=set(run.logs.filter(kind='cooldown').values_list('recipient',flat=True))
        for email,person in sorted(people.items()):
            if email in members:continue
            if not qualifies(person,config):
                if email not in skips:
                    detail=('Не выполнено дополнительное условие открытий.' if person['state']==config['target'] else
                        f'Группа «{person["label"]}» не соответствует сценарию. {person["reason"]}')
                    log(run,'not_eligible',detail,now,email)
                continue
            previous=AutomationSimulationMember.objects.filter(run__scenario_id=run.scenario_id,recipient=email).exclude(run=run).order_by('-entered_at').first()
            if previous and previous.entered_at+timedelta(days=config['cooldown_days'])>now:
                if email not in cooldowns:log(run,'cooldown','Не завершился срок повторного входа в этот сценарий.',now,email)
                continue
            member=AutomationSimulationMember.objects.create(run=run,recipient=email,entered_at=now,
                next_at=now+timedelta(minutes=steps[0]['delay_minutes']))
            members[email]=member;log(run,'entered','Получатель соответствует условиям; ожидает первый шаг.',now,email)
        for member in sorted(members.values(),key=lambda m:(m.next_at,m.pk)):
            if member.state!='waiting':continue
            gate=frequency.TestFrequencyPolicy.objects.get(pk=1)
            if member.frequency_base_at and (not gate.enabled or member.frequency_revision!=gate.revision):
                member.next_at=member.frequency_base_at;member.frequency_base_at=None
                member.save(update_fields=['next_at','frequency_base_at'])
            person=people.get(member.recipient)
            if not qualifies(person,config):
                member.state='excluded' if person and person['state']=='excluded' else 'exited'
                member.detail='Тестовая отписка или окончательная ошибка.' if member.state=='excluded' else 'Условия аудитории или открытий больше не выполняются.'
                member.save(update_fields=['state','detail']);log(run,member.state,member.detail,now,member.recipient);continue
            if member.next_at>now or performed>=budget:continue
            allowed=frequency_at(member.recipient,config,now)
            common=frequency.plan(member.recipient,now)
            allowed=max(allowed,common['allowed_at'])
            if allowed>now:
                if common['allowed_at']>now:
                    member.frequency_base_at=member.next_at;member.frequency_revision=common['revision']
                member.next_at=allowed;member.detail=(frequency.explanation(common,allowed) if common['allowed_at']>now else
                    'Шаг отложен: учитываются имитации во всех сценариях.')
                member.save(update_fields=['next_at','detail','frequency_base_at','frequency_revision']);log(run,'frequency',member.detail,now,member.recipient);continue
            step=steps[member.step_index]
            AutomationSimulationAction.objects.create(member=member,campaign_id=step['campaign_id'],recipient=member.recipient,
                step_index=member.step_index,occurred_at=now,snapshot=step)
            log(run,'simulated',f'Шаг {member.step_index+1}: {step["campaign_name"]}. Только имитация, без отправки и доставки.',now,member.recipient)
            performed+=1;member.step_index+=1
            member.frequency_base_at=None
            if member.step_index==len(steps):
                member.state='completed';member.detail='Все шаги имитированы. Автоматического повторного прохода в этом запуске нет.'
            else:
                member.next_at=now+timedelta(minutes=max(steps[member.step_index]['delay_minutes'],config['min_interval_minutes']))
                member.detail='Ожидает задержку следующего шага и ограничение частоты.'
            member.save(update_fields=['state','step_index','next_at','detail','frequency_base_at'])
    return performed


@transaction.atomic
def tick(minutes=0):
    if type(minutes) is not int or not 0<=minutes<=10080:raise ValueError('Шаг часов: от 0 до 10080 минут (7 дней).')
    clock=lock_clock()
    if clock.clock_at.year>9997:raise ValueError('Достигнут предел часов имитации.')
    clock.clock_at+=timedelta(minutes=minutes);clock.save(update_fields=['clock_at'])
    frequency.advance_gate(clock.clock_at)
    return process(clock)


@transaction.atomic
def control(run_id,action):
    clock=lock_clock();run=AutomationSimulationRun.objects.select_for_update().get(pk=run_id)
    previous=run.state
    if action=='pause':
        if run.state not in ('running','paused'):raise ValueError('Отменённую имитацию нельзя поставить на паузу.')
        if run.state=='paused':return run
        run.state='paused';kind='paused';detail='Обработка приостановлена. Общие часы могут продолжать движение.'
    elif action=='resume':
        from .campaign_approval import require_steps
        require_steps(run.snapshot)
        if run.state not in ('paused','running'):raise ValueError('Отменённую имитацию нельзя продолжить.')
        if run.state=='running':return run
        run.state='running';kind='resumed';detail='Имитация продолжена. Условия и ограничения будут перепроверены.'
    elif action=='cancel':
        if run.state=='cancelled':return run
        run.state='cancelled';kind='cancelled';detail='Имитация отменена. История сохранена.'
        run.members.filter(state='waiting').update(state='cancelled',detail='Оставшиеся шаги отменены.')
    else:raise ValueError('Доступны пауза, продолжение или отмена.')
    run.save(update_fields=['state']);log(run,kind,detail,clock.clock_at,audit_before={'state':previous})
    if action=='resume':process(clock)
    return run


def scenario_record(row):
    run=row.runs.order_by('-pk').first()
    return {'id':row.pk,'name':row.name,'revision':row.revision,'config':row.config,
            'run':{'id':run.pk,'state':run.state,'revision':run.snapshot['revision']} if run else None}


def report(params):
    clock=AutomationSimulationClock.objects.filter(pk=1).first()
    runs=AutomationSimulationRun.objects.select_related('scenario').order_by('-pk')
    requested=params.get('run_id','')
    if requested and (not requested.isascii() or not requested.isdigit() or len(requested)>10):raise ValueError('Проверьте идентификатор имитации.')
    run=runs.filter(pk=int(requested)).first() if requested else runs.first()
    if requested and not run:raise ValueError('Имитация не найдена.')
    result={'mode':'simulation','can_send':False,'clock_at':clock.clock_at if clock else None,'kinds':KINDS,
            'frequency_clock_at':frequency.TestFrequencyPolicy.objects.filter(pk=1).values_list('clock_at',flat=True).first(),
            'runs':[{'id':r.pk,'name':r.snapshot['name'],'state':r.state,'revision':r.snapshot['revision']} for r in runs[:200]],
            'run':None,'members':[],'history':[],'pagination':{'page':1,'pages':1,'total':0,'has_next':False,'has_previous':False}}
    if not run:return result
    query=params.get('query','').strip().casefold();kind=params.get('kind','');direction=params.get('direction','desc')
    if len(query)>254 or kind not in ('',*KINDS) or direction not in ('asc','desc'):raise ValueError('Проверьте фильтры журнала.')
    logs=run.logs.all()
    if query:logs=logs.filter(recipient__icontains=query)
    if kind:logs=logs.filter(kind=kind)
    try:page=int(params.get('page','1'))
    except ValueError:raise ValueError('Проверьте страницу.')
    if not 1<=page<=2147483647:raise ValueError('Проверьте страницу.')
    logs=logs.order_by(('' if direction=='asc' else '-')+'occurred_at',('' if direction=='asc' else '-')+'pk')
    paginator=Paginator(logs,25);current=paginator.page(min(page,paginator.num_pages))
    counts=Counter(run.members.values_list('state',flat=True))
    result.update(run={'id':run.pk,'state':run.state,'snapshot':run.snapshot,'counts':dict(counts),
                       'actions':AutomationSimulationAction.objects.filter(member__run=run).count()},
        members=[{'recipient':m.recipient,'state':m.state,'step':m.step_index,'steps_total':len(run.snapshot['steps']),
                  'entered_at':m.entered_at,'next_at':m.next_at if m.state=='waiting' else None,'detail':m.detail}
                 for m in run.members.filter(recipient__icontains=query).order_by('recipient')[:100]],
        history=[{'recipient':e.recipient,'kind':e.kind,'detail':e.detail,'occurred_at':e.occurred_at} for e in current],
        pagination={'page':current.number,'pages':paginator.num_pages,'total':paginator.count,'has_next':current.has_next(),'has_previous':current.has_previous()})
    return result

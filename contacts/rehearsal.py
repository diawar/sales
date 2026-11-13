"""Isolated end-to-end rehearsals. No transport, DNS or other network calls."""
from collections import Counter
from datetime import timedelta
from bs4 import BeautifulSoup
from django.core.paginator import Paginator
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from . import automation_simulation as engine
from .activity_audiences import classification, event_rows, LABELS
from .campaign_mail_check import snapshots, diagnosis
from .email_policy import BAD_CODES
from .mail_tracking import prepare_preview, observe
from .models import (AutomationScenario, EmailCampaign, EmailTemplate, Address, EmailExclusion,
    CampaignRehearsal, RehearsalLetter, RehearsalObservation, RehearsalLog)
from .subscriptions import has_revocation, apply_token
from . import rehearsal_delivery as delivery
from . import rehearsal_safety as safety
from . import test_frequency as frequency
from . import action_audit as audit

KINDS = {'started':'Начало', 'skipped':'Не вошёл', 'waiting':'Ожидание', 'prepared':'Письмо подготовлено',
    'blocked':'Проверка не пройдена', 'frequency':'Ограничение частоты', 'exited':'Выход из условий',
    'excluded':'Исключён', 'open':'Открытие', 'click':'Переход', 'unsubscribe':'Отписка',
    'paused':'Пауза', 'resumed':'Продолжение', 'cancelled':'Отмена'}
KINDS.update(delivery.KINDS)
KINDS.update({'safety_stopped':'Автоматическая остановка','safety_configured':'Пороги защиты','global_frequency':'Общая частота по клиенту'})


def log(row, kind, detail, email='',audit_before=None):
    RehearsalLog.objects.create(rehearsal=row,recipient=email,kind=kind,detail=detail,occurred_at=row.clock_at)
    audit.run_event(row,kind,detail,rehearsal=True,before=audit_before)


def capture(event):
    """Called by the normal pixel/redirect/unsubscribe handlers, including first-event retries."""
    letter=RehearsalLetter.objects.select_related('rehearsal').filter(message_id=event.message_id).first()
    if not letter or not letter.message.is_test:return
    _,created=RehearsalObservation.objects.get_or_create(event=event,
        defaults={'rehearsal':letter.rehearsal,'virtual_at':letter.rehearsal.clock_at})
    if created:log(letter.rehearsal,event.kind,'Тестовое событие собственного учёта; доставка не подтверждена.',letter.recipient)


def people(row):
    events=[{**e,'occurred_at':parse_datetime(e['occurred_at'])} for e in row.baseline]
    for obs in row.observations.select_related('event__message').filter(virtual_at__lte=row.clock_at):
        events.append({'recipient':obs.event.message.recipient,'kind':obs.event.kind,
            'occurred_at':obs.virtual_at,'message_key':str(obs.event.message_id),'error_type':''})
    for event in delivery.RehearsalDeliveryEvent.objects.filter(letter__rehearsal=row,occurred_at__lte=row.clock_at).select_related('letter'):
        if event.kind not in ('permanent_error','complaint','test_delivered'):continue
        events.append({'recipient':event.letter.recipient,'kind':'delivered' if event.kind=='test_delivered' else 'error',
            'occurred_at':event.occurred_at,'message_key':str(event.letter.message_id),'error_type':'permanent'})
    _,_,rows,*_=classification({**row.snapshot['config'],'as_of':row.clock_at.isoformat()},test_events=events)
    return {r['email']:r for r in rows}


def restriction(email):
    address=Address.objects.filter(email__iexact=email).first()
    if has_revocation(email):return 'Есть рабочая отписка. Репетиция не снимает запрет.'
    if address and (address.archived or address.suppression_reason or address.validation_result.get('code') in BAD_CODES
        or EmailExclusion.objects.filter(address=address,removed_at=None).exists()):
        return 'В рабочей базе есть исключение или ошибка адреса. Данные не изменены.'
    return ''


def preview_html(html):
    soup=BeautifulSoup(html,'html.parser')
    for image in soup.find_all('img'):
        if image.get('width')=='1' and image.get('height')=='1':image.decompose()
    for anchor in soup.find_all('a',href=True):del anchor['href']
    # Raw tracking/subscription tokens are never stored in the rehearsal preview.
    return str(soup)


@transaction.atomic
def start(request,data):
    engine.simulation_only(data)
    gate=frequency.lock_gate()
    sid=engine.integer(data,'scenario_id',None,1,2147483647)
    scenario=AutomationScenario.objects.get(pk=sid)
    if data.get('revision')!=scenario.revision or type(data.get('revision')) is not int:
        raise ValueError('Сценарий изменился. Обновите список.')
    values=data.get('values',{})
    if not isinstance(values,dict) or any(k not in ('name','company','city') or not isinstance(v,str) or len(v)>250 for k,v in values.items()):
        raise ValueError('Персонализация: имя, компания, город — до 250 символов.')
    guard=safety.policy(data.get('safety_policy',{}))
    config=engine.configuration(scenario.config);now=gate.clock_at if gate.enabled else timezone.now();steps=[]
    for step in config['steps']:
        campaign=EmailCampaign.objects.select_related('dispatch_queue').get(pk=step['campaign_id'])
        from .campaign_approval import require_approved
        approval_version=require_approved(campaign,recipients=True)
        template,sender,version,basis=snapshots(campaign)
        steps.append({**step,'approval_version':approval_version,'campaign_name':campaign.name,'template':template,'sender':sender,
            'template_id':template.get('id') or campaign.template_id,'version':version,'basis':basis})
    source,_=event_rows({'source':config['source'],'campaign_id':config['source_campaign_id']},now)
    baseline=[];emails=set()
    for event in source:
        emails.add(event['recipient'].casefold())
        if len(baseline)>=10000 or len(emails)>1000:raise ValueError('Сузьте источник: до 1000 получателей и 10 000 тестовых событий.')
        baseline.append({**event,'occurred_at':event['occurred_at'].isoformat()})
    row=CampaignRehearsal.objects.create(scenario=scenario,clock_at=now,baseline=baseline,
        snapshot={'name':scenario.name,'revision':scenario.revision,'config':config,'steps':steps,'values':values,'delivery_policy':delivery.POLICY.copy(),
            'safety_policy':guard,'safety_revision':1})
    log(row,'started','Зафиксированы тестовый источник, сценарий, письма и отправители. SMTP отсутствует. Местные ограничения дополнены общим контролем частоты по email.')
    members=[]
    for email,person in sorted(people(row).items()):
        if not engine.qualifies(person,config):
            detail='Не выполнено дополнительное условие открытий.' if person['state']==config['target'] else person['label']+': '+person['reason']
            log(row,'skipped',detail,email);continue
        members.append({'email':email,'state':'waiting','step':0,
            'next_at':(now+timedelta(minutes=steps[0]['delay_minutes'])).isoformat(),'detail':'Ожидание первого шага.'})
        log(row,'waiting','Вход в сценарий. Первый срок: '+members[-1]['next_at'],email)
    row.members=members;row.save(update_fields=['members'])
    process(request,row)
    return row


def process(request,row):
    safety.evaluate(row,log)
    if row.state!='running':return
    from .campaign_approval import require_steps
    try:require_steps(row.snapshot)
    except ValueError as exc:
        row.state='paused';row.save(update_fields=['state'])
        log(row,'paused','Остановка согласования: '+str(exc))
        return
    audience=people(row);config=row.snapshot['config'];now=row.clock_at;budget=100
    delivery.process(request,row,audience,log,restriction,engine.qualifies,diagnosis)
    for member in row.members:
        if member['state']!='waiting':continue
        gate=frequency.TestFrequencyPolicy.objects.get(pk=1)
        if member.get('global_base_at') and (not gate.enabled or member.get('global_frequency_revision')!=gate.revision):
            member['next_at']=member.pop('global_base_at')
        email=member['email'];person=audience.get(email);blocked=delivery.reason(row,email) or restriction(email)
        if blocked or not engine.qualifies(person,config):
            excluded=blocked or person and person['state']=='excluded'
            member['state']='excluded' if excluded else 'exited'
            member['detail']=blocked or ('Отписка или окончательная ошибка.' if excluded else 'Условия аудитории или открытий больше не выполняются.')
            log(row,member['state'],member['detail'],email);continue
        if row.letters.filter(recipient=email,delivery_state__in=delivery.PENDING).exists():
            member['detail']='Следующие шаги ожидают тестового результата доставки или повтора.';continue
        if row.letters.filter(recipient=email,delivery_state='exhausted').exists():
            member.update(state='blocked',detail='Лимит условных попыток исчерпан.');continue
        if parse_datetime(member['next_at'])>now or not budget:continue
        history=list(row.letters.filter(recipient=email,prepared_at__lte=now).order_by('prepared_at').values_list('prepared_at',flat=True))
        next_at=now
        if history:next_at=max(next_at,history[-1]+timedelta(minutes=config['min_interval_minutes']))
        for days,limit in ((1,config['max_24h']),(7,config['max_7d'])):
            dates=[d for d in history if d>now-timedelta(days=days)]
            if len(dates)>=limit:next_at=max(next_at,dates[len(dates)-limit]+timedelta(days=days))
        common=frequency.plan(email,now)
        next_at=max(next_at,common['allowed_at'])
        if common['allowed_at']>now:
            member.setdefault('global_base_at',member['next_at'])
            member['next_at']=next_at.isoformat();member['detail']=frequency.explanation(common,next_at)
            member['global_frequency_revision']=common['revision']
            log(row,'global_frequency',member['detail'],email);continue
        if next_at>now:
            member['next_at']=next_at.isoformat();member['detail']='Ограничение частоты подготовки тестовых писем.'
            log(row,'frequency',member['detail'],email);continue
        step=row.snapshot['steps'][member['step']]
        check=diagnosis(request,step['template'],step['sender'],email,row.snapshot['values'])
        check['checks'].append({'code':'exclusions','status':'pass','title':'Исключения','detail':'Запретов не найдено; тестовая аудитория соответствует условиям.'})
        check['checks'].append({'code':'frequency','status':'pass','title':'Частота подготовки','detail':'Задержка, местные и общие ограничения частоты перепроверены.'})
        check['checks'].append({'code':'consent','status':'pending','title':'Согласие','detail':'Рабочее согласие не создаётся и не подтверждается репетицией.'})
        if not check['can_prepare']:
            member['state']='blocked';member['detail']='; '.join(c['title']+': '+c['detail'] for c in check['checks'] if c['status']=='fail')
            log(row,'blocked',member['detail'],email);continue
        try:
            with transaction.atomic():
                result=prepare_preview(request,EmailTemplate.objects.get(pk=step['template_id']),email,row.snapshot['values'],
                    campaign=EmailCampaign.objects.get(pk=step['campaign_id']),template_snapshot=step['template'],sender_snapshot=step['sender'],
                    metadata={'rehearsal_id':row.pk,'scenario_revision':row.snapshot['revision'],'step_index':member['step'],'version':step['version']},
                    subscription_scope='rehearsal:'+str(row.pk))
                RehearsalLetter.objects.create(rehearsal=row,message_id=result['id'],recipient=email,step_index=member['step'],
                    prepared_at=now,checks=check,preview_html=preview_html(result['html']))
        except (ValueError,EmailTemplate.DoesNotExist) as exc:
            member['state']='blocked';member['detail']=str(exc);log(row,'blocked',str(exc),email);continue
        budget-=1;member['step']+=1;member['detail']='Проверено и подготовлено. Не отправлено; доставка неизвестна.'
        member.pop('global_base_at',None);member.pop('global_frequency_revision',None)
        log(row,'prepared',f'Шаг {member["step"]}: '+member['detail'],email)
        if member['step']==len(row.snapshot['steps']):member['state']='completed'
        else:
            delay=max(row.snapshot['steps'][member['step']]['delay_minutes'],config['min_interval_minutes'])
            member['next_at']=(now+timedelta(minutes=delay)).isoformat()
    row.save(update_fields=['members'])


@transaction.atomic
def act(request,rid,data):
    engine.simulation_only(data)
    gate=frequency.lock_gate()
    row=CampaignRehearsal.objects.select_for_update().get(pk=rid)
    frequency.sync_clock(row,gate)
    action=data.get('action')
    if action=='resume':
        from .campaign_approval import require_steps
        require_steps(row.snapshot)
    if action=='advance':
        if row.state!='running':raise ValueError('Сначала продолжите репетицию.')
        row.clock_at+=timedelta(minutes=engine.integer(data,'minutes',0,0,10080));row.save(update_fields=['clock_at'])
        frequency.advance_gate(row.clock_at)
    elif action in ('pause','resume','cancel'):
        if row.state=='cancelled':raise ValueError('Репетиция уже отменена. История сохранена.')
        if action!='cancel' and (row.state=='safety_stopped' or row.snapshot.get('safety_stop')):
            raise ValueError('Репетиция остановлена защитой. Проверьте причину и создайте новую; обычное продолжение заблокировано.')
        state={'pause':'paused','resume':'running','cancel':'cancelled'}[action]
        if row.state!=state:
            previous=row.state
            row.state=state;row.save(update_fields=['state'])
            if action=='cancel':
                for member in row.members:
                    if member['state'] in ('waiting','safety_stopped'):member.update(state='cancelled',detail='Оставшиеся шаги отменены.')
                row.save(update_fields=['members'])
                for email in row.letters.filter(delivery_state__in=delivery.PENDING).values_list('recipient',flat=True).distinct():
                    delivery.stop_pending(row,email,'Репетиция отменена.',log)
            log(row,{'pause':'paused','resume':'resumed','cancel':'cancelled'}[action],'Управление репетицией.',audit_before={'state':previous})
    elif action=='safety_configure':
        safety.configure(row,data,log)
    elif action=='delivery_event':
        lid=engine.integer(data,'letter_id',None,1,2147483647)
        letter=row.letters.select_related('message').filter(pk=lid).first()
        if not letter:raise ValueError('Письмо этой репетиции не найдено.')
        delivery.outcome(row,letter,data,log)
        row.save(update_fields=['members'])
    elif action=='event':
        lid=engine.integer(data,'letter_id',None,1,2147483647)
        letter=row.letters.select_related('message__unsubscribe_token__subscription').filter(pk=lid).first()
        if not letter:raise ValueError('Письмо этой репетиции не найдено.')
        kind=data.get('kind')
        if kind=='unsubscribe':
            token=letter.message.unsubscribe_token
            if not token.subscription.is_test or token.subscription.scope!='rehearsal:'+str(row.pk):raise ValueError('Нужна отдельная тестовая подписка.')
            apply_token(token)
        elif kind in ('open','click'):
            link=letter.message.links.filter(kind=kind).order_by('pk').first()
            if not link:raise ValueError('В этом письме нет ссылки перехода.')
            observe(link)
        else:raise ValueError('Доступны открытие, переход и отписка.')
    elif action!='tick':raise ValueError('Неизвестное действие репетиции.')
    process(request,row)
    return row


def report(row,params):
    query=params.get('query','').strip().casefold();kind=params.get('kind','');state=params.get('state','');direction=params.get('direction','desc')
    if len(query)>254 or kind and kind not in KINDS or state and state not in ('waiting','completed','blocked','exited','excluded','cancelled','safety_stopped') or direction not in ('asc','desc'):
        raise ValueError('Проверьте фильтры репетиции.')
    ds=params.get('delivery_state','')
    if ds and ds not in delivery.STATES:raise ValueError('Неизвестное состояние тестовой доставки.')
    members=[m for m in row.members if query in m['email'] and (not state or m['state']==state)]
    audience=people(row)
    members=[{**m,'activity':audience.get(m['email'],{}).get('label','Мало данных')} for m in members]
    pending={}
    for letter in row.letters.filter(delivery_state__in=delivery.PENDING).order_by('retry_at','pk'):
        pending.setdefault(letter.recipient,[]).append(letter)
    for member in members:
        letters_pending=pending.get(member['email'],[])
        if any(l.delivery_state=='awaiting_result' for l in letters_pending):
            member.update(next_at=None,next_note='Ожидается тестовый результат')
        elif letters_pending:
            member.update(next_at=min(l.retry_at for l in letters_pending),next_note='Повтор письма')
    logs=row.logs.all()
    if query:logs=logs.filter(recipient__icontains=query)
    if kind:logs=logs.filter(kind=kind)
    page=engine.integer({'page':int(params.get('page',1))},'page',1,1,2147483647)
    paginator=Paginator(logs.order_by('pk' if direction=='asc' else '-pk'),25);current=paginator.page(min(page,paginator.num_pages))
    letters=row.letters.filter(recipient__icontains=query).select_related('message').order_by('-pk')
    if ds:letters=letters.filter(delivery_state=ds)
    return {'id':row.pk,'name':row.snapshot['name'],'revision':row.snapshot['revision'],'state':row.state,
        'clock_at':row.clock_at,'mode':'simulation','can_send':False,'delivered':None,'kinds':KINDS,'safety':safety.assessment(row),
        'frequency_clock_at':frequency.TestFrequencyPolicy.objects.filter(pk=1).values_list('clock_at',flat=True).first(),
        'counts':dict(Counter(m['state'] for m in row.members)),'total_members':len(row.members),'members':members[:100],
        'events':{kind:row.observations.filter(event__kind=kind).values('event__message_id').distinct().count() for kind in ('open','click','unsubscribe')},
        'delivery_states':delivery.STATES,'delivery_policy':row.snapshot.get('delivery_policy',delivery.POLICY),
        'delivery_counts':dict(Counter(row.letters.values_list('delivery_state',flat=True))),
        'delivery_event_counts':{kind:delivery.RehearsalDeliveryEvent.objects.filter(letter__rehearsal=row,kind=kind).values('letter_id').distinct().count() for kind in delivery.OUTCOMES},
        'members_matched':len(members),'letters_total':row.letters.count(),
        'letters':[{'id':l.pk,'message_id':str(l.message_id),'recipient':l.recipient,'step':l.step_index+1,
            'campaign_id':l.message.campaign_id,'subject':l.message.subject,'prepared_at':l.prepared_at,
            'delivery_state':l.delivery_state,'attempt':l.attempt_count,'retry_at':l.retry_at,'delivery_detail':l.delivery_detail,
            'events':list(l.message.events.values_list('kind',flat=True))} for l in letters[:100]],
        'history':list(current.object_list.values('recipient','kind','detail','occurred_at')),
        'pagination':{'page':current.number,'pages':paginator.num_pages,'total':paginator.count}}

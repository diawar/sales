"""Synthetic delivery outcomes only. No production suppressions or transport calls."""
import uuid
from datetime import timedelta
from .models import RehearsalDeliveryEvent
from . import action_audit as audit

POLICY = {'max_attempts':3,'retry_minutes':[15,60]}
KINDS = {'temporary_error':'Временная ошибка (тест)', 'permanent_error':'Постоянная ошибка (тест)',
    'complaint':'Жалоба (тест)', 'test_delivered':'Доставка (имитация)',
    'retry_started':'Повтор имитирован', 'retry_exhausted':'Попытки исчерпаны', 'retry_stopped':'Повтор остановлен'}
STATES = {'untested':'Результат не задан', 'retry':'Ожидает повтора', 'awaiting_result':'Ожидает тестового результата',
    'test_delivered':'Условно доставлено', 'permanent_error':'Постоянная ошибка', 'complaint':'Жалоба',
    'exhausted':'Попытки исчерпаны', 'stopped':'Повтор остановлен'}
OUTCOMES = ('temporary_error','permanent_error','complaint','test_delivered')
PENDING = ('retry','awaiting_result')


def reason(row,email):
    # A sticky, experiment-local prohibition; never read/write Address exclusions here.
    events=RehearsalDeliveryEvent.objects.filter(letter__rehearsal=row,letter__recipient=email)
    event=events.filter(kind='complaint').first() or events.filter(kind='permanent_error').first()
    return ('Тестовая жалоба: дальнейшие шаги запрещены.' if event.kind=='complaint' else
        'Постоянная тестовая ошибка: дальнейшие шаги запрещены.') if event else ''


def stop_member(row,email,detail,state='excluded'):
    for member in row.members:
        if member['email']==email and member['state']!='cancelled':member.update(state=state,detail=detail)


def stop_pending(row,email,detail,log,exclude_id=None):
    for letter in row.letters.filter(recipient=email,delivery_state__in=PENDING).exclude(pk=exclude_id):
        letter.delivery_state='stopped';letter.retry_at=None;letter.delivery_detail=detail
        letter.save(update_fields=['delivery_state','retry_at','delivery_detail'])
        log(row,'retry_stopped',f'Письмо #{letter.pk}: '+detail,email)


def outcome(row,letter,data,log):
    if not letter.message.is_test:raise ValueError('Доступны только тестовые письма.')
    kind=data.get('kind');attempt=data.get('attempt');detail=data.get('detail','')
    if kind not in OUTCOMES or type(attempt) is not int or not 1<=attempt<=letter.attempt_count:
        raise ValueError('Проверьте тестовый результат и номер условной попытки.')
    if not isinstance(detail,str) or len(detail)>500:raise ValueError('Описание ошибки: до 500 символов.')
    detail=detail.strip()
    try:event_id=uuid.UUID(str(data.get('event_id','')))
    except (ValueError,AttributeError):raise ValueError('Нужен UUID тестового события.') from None
    previous=RehearsalDeliveryEvent.objects.filter(event_id=event_id).first()
    if previous:
        if (previous.letter_id,previous.attempt,previous.kind,previous.detail)!=(letter.pk,attempt,kind,detail):
            raise ValueError('Этот ID события уже использован для другого результата.')
        return False
    previous=letter.delivery_events.filter(attempt=attempt,kind=kind).first()
    if previous:
        if previous.detail!=detail:raise ValueError('Результат этой попытки уже записан с другим описанием.')
        return False
    if kind in ('temporary_error','test_delivered'):
        if row.state=='cancelled' or reason(row,letter.recipient) or attempt!=letter.attempt_count or letter.delivery_state not in ('untested','awaiting_result'):
            raise ValueError('Попытка уже завершена, остановлена или запрещена. Обновите репетицию.')
    RehearsalDeliveryEvent.objects.create(event_id=event_id,letter=letter,attempt=attempt,kind=kind,
        detail=detail,occurred_at=row.clock_at)
    row.snapshot.setdefault('delivery_policy',POLICY.copy())
    row.save(update_fields=['snapshot'])
    letter.retry_at=None;letter.delivery_detail=detail or KINDS[kind]
    if kind=='temporary_error':
        policy=row.snapshot['delivery_policy']
        if row.snapshot.get('safety_stop'):
            letter.delivery_state='stopped'
        elif letter.attempt_count>=policy['max_attempts']:
            letter.delivery_state='exhausted'
            stop_member(row,letter.recipient,'Временные ошибки: лимит попыток исчерпан. Следующие шаги остановлены.','blocked')
            stop_pending(row,letter.recipient,'У другого письма этого получателя исчерпаны попытки.',log,letter.pk)
        else:
            letter.delivery_state='retry'
            letter.retry_at=row.clock_at+timedelta(minutes=policy['retry_minutes'][letter.attempt_count-1])
            for member in row.members:
                if member['email']==letter.recipient:member['detail']='Временная тестовая ошибка. Следующие шаги ожидают результата повтора.'
    else:
        letter.delivery_state='complaint' if letter.delivery_state=='complaint' else kind
        if kind in ('permanent_error','complaint'):
            detail_reason=reason(row,letter.recipient);stop_member(row,letter.recipient,detail_reason)
            stop_pending(row,letter.recipient,detail_reason,log,letter.pk)
    letter.save(update_fields=['delivery_state','retry_at','delivery_detail'])
    log(row,kind,f'Письмо #{letter.pk}, условная попытка {attempt}. '+letter.delivery_detail,letter.recipient)
    audit.record('test_event','rehearsal',row.pk,row.snapshot['name'],campaigns=[letter.message.campaign_id],
        after={'kind':kind,'letter_id':letter.pk,'attempt':attempt,'test_at':row.clock_at},summary='Записан тестовый результат доставки. SMTP не использовался.')
    if letter.delivery_state=='exhausted':log(row,'retry_exhausted',f'Письмо #{letter.pk}: достигнут предел {letter.attempt_count} попыток.',letter.recipient)
    return True


def process(request,row,audience,log,restriction,qualifies,diagnosis):
    """At most one retry per letter per tick; never invent a delivery outcome."""
    count=0
    for letter in row.letters.filter(delivery_state__in=PENDING).order_by('retry_at','pk'):
        blocked=reason(row,letter.recipient) or restriction(letter.recipient)
        person=audience.get(letter.recipient)
        exhausted=row.letters.filter(recipient=letter.recipient,delivery_state='exhausted').exists()
        if blocked or exhausted or not qualifies(person,row.snapshot['config']):
            detail=blocked or ('Исчерпаны попытки другого письма.' if exhausted else 'Условия аудитории или открытий больше не выполняются.')
            stop_pending(row,letter.recipient,detail,log)
            state='excluded' if blocked or person and person['state']=='excluded' else 'blocked' if exhausted else 'exited'
            stop_member(row,letter.recipient,detail,state);continue
        if letter.delivery_state!='retry' or letter.retry_at>row.clock_at or count>=100:continue
        step=row.snapshot['steps'][letter.step_index]
        check=diagnosis(request,step['template'],step['sender'],letter.recipient,row.snapshot['values'])
        if not check['can_prepare']:
            detail='Повтор не прошёл проверки: '+'; '.join(c['title'] for c in check['checks'] if c['status']=='fail')
            stop_pending(row,letter.recipient,detail,log);stop_member(row,letter.recipient,detail,'blocked');continue
        letter.attempt_count+=1;letter.retry_at=None;letter.delivery_state='awaiting_result'
        letter.delivery_detail='Повтор только имитирован; задайте тестовый результат. SMTP не используется.'
        letter.save(update_fields=['attempt_count','retry_at','delivery_state','delivery_detail'])
        log(row,'retry_started',f'Письмо #{letter.pk}, условная попытка {letter.attempt_count}. '+letter.delivery_detail,letter.recipient)
        count+=1
    return count

"""Campaign planning only. No transport calls or scheduler execution in this stage."""
from zoneinfo import ZoneInfo
from django.db import transaction, IntegrityError
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.views.decorators.http import require_http_methods, require_POST
from .models import Audience, Address, Contact, EmailTemplate, CampaignSender, EmailCampaign, CampaignQueue
from .email_policy import sending_state
from .template_tools import check_template
from .views import body, error
from . import action_audit as audit

TIMEZONES = ('Asia/Qyzylorda', 'Asia/Almaty', 'UTC')


def audience_selection(data):
    ids = data.get('audience_ids')
    if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(type(i) is not int or i < 1 for i in ids):
        raise ValueError('Выберите от 1 до 100 аудиторий.')
    ids = set(ids)
    parents = dict(Audience.objects.values_list('id', 'parent_id'))
    if not ids.issubset(parents): raise ValueError('Одна из аудиторий больше не существует.')
    selected = set(ids)
    while True:
        extra = {pk for pk, parent in parents.items() if parent in selected} - selected
        if not extra: break
        selected.update(extra)
    return sorted(ids), selected


def recipients(data):
    ids, expanded = audience_selection(data)
    contacts = Contact.objects.filter(audiences__id__in=expanded).distinct()
    addresses = Address.objects.filter(pk__in=contacts.exclude(address=None).values('address_id'))
    # Older data may contain case variants. Group with the same normalization as
    # imports and apply the strictest restriction to the whole mailbox.
    groups = {email.strip().casefold(): 'ready' for email in addresses.values_list('email',flat=True)}
    priority = {'archived':0, 'suppressed':1, 'invalid':2, 'pending':3, 'ready':4}
    for address in Address.objects.only('email','archived','suppression_reason','validation_result','validation').iterator(chunk_size=1000):
        state = sending_state(address)['code']
        key = 'archived' if address.archived else 'suppressed' if address.suppression_reason else state
        email = address.email.strip().casefold()
        if email in groups and priority[key] < priority[groups[email]]: groups[email] = key
    from .subscriptions import revoked_emails
    for email in revoked_emails():
        if email in groups and groups[email]!='archived':groups[email]='suppressed'
    totals = {key:0 for key in priority}
    for key in groups.values(): totals[key] += 1
    contact_total = contacts.count()
    missing = contacts.filter(address=None).count()
    result = {'contacts': contact_total, 'clients': contacts.order_by().values('client_id').distinct().count(),
              'without_email': missing, 'unique_emails': len(groups),
              'duplicates': contact_total-missing-len(groups), **totals,
              'calculated_at': timezone.now().isoformat()}
    return result


def sender_record(row):
    return {k: getattr(row, k) for k in ('id', 'name', 'email', 'reply_to')}


@require_http_methods(['GET', 'POST'])
def senders(request):
    if request.method == 'GET':
        return JsonResponse({'senders': [sender_record(s) for s in CampaignSender.objects.order_by('name')],
                             'timezones': TIMEZONES, 'transport_connected': False})
    data = body(request)
    try:
        values = {}
        for field, limit in [('name',160), ('email',254), ('reply_to',254)]:
            value = data.get(field, '')
            if not isinstance(value, str) or len(value) > limit or '\r' in value or '\n' in value: raise ValueError('Проверьте поля отправителя.')
            values[field] = value.strip()
        if not values['name']: raise ValueError('Укажите имя отправителя.')
        values['email'] = values['email'].casefold()
        values['reply_to'] = values['reply_to'].casefold()
        validate_email(values['email'])
        if values['reply_to']: validate_email(values['reply_to'])
        with transaction.atomic():
            if CampaignSender.objects.filter(email__iexact=values['email']).exists(): return error('Отправитель с таким email уже есть.',409)
            row = CampaignSender.objects.create(**values)
        return JsonResponse(sender_record(row),status=201)
    except (ValueError, ValidationError) as exc:
        return error(str(exc) if isinstance(exc,ValueError) else 'Проверьте email отправителя и адрес для ответов.')
    except IntegrityError: return error('Отправитель с таким email уже есть.',409)


def plan(data, lock=False):
    name = data.get('name','')
    if not isinstance(name,str) or not 1 <= len(name.strip()) <= 160: raise ValueError('Название рассылки: от 1 до 160 символов.')
    ids, _ = audience_selection(data)
    for key in ('template_id','sender_id','template_revision'):
        if type(data.get(key)) is not int or data[key] < 1: raise ValueError('Выберите шаблон и отправителя.')
    templates = EmailTemplate.objects.select_for_update() if lock else EmailTemplate.objects
    template = templates.filter(pk=data['template_id']).first()
    sender = CampaignSender.objects.filter(pk=data['sender_id']).first()
    if not template or not sender: raise ValueError('Шаблон или отправитель больше не существует.')
    if template.revision != data['template_revision']: raise ValueError('Шаблон изменился. Обновите список и выберите его заново.')
    mode = data.get('schedule_mode','manual')
    zone = data.get('schedule_timezone','Asia/Qyzylorda')
    if mode not in ('manual','scheduled') or zone not in TIMEZONES: raise ValueError('Проверьте расписание и часовой пояс.')
    scheduled = None
    if mode == 'scheduled':
        value = data.get('scheduled_at','')
        if not isinstance(value,str) or len(value)>40: raise ValueError('Укажите дату и время.')
        try: scheduled = parse_datetime(value)
        except ValueError: scheduled = None
        if not scheduled: raise ValueError('Укажите корректную дату и время.')
        if timezone.is_naive(scheduled): scheduled = timezone.make_aware(scheduled,ZoneInfo(zone))
        if scheduled <= timezone.now(): raise ValueError('Дата рассылки должна быть в будущем.')
    counts = recipients(data)
    template_data = {k:getattr(template,k) for k in ('id','name','revision','subject','preheader','html')}
    checks = check_template(template.subject, template.preheader, template.html, {})
    warnings = ['Это план рассылки: доступна только очередь имитации, SMTP не подключён.',
                'Количество пересчитывается при проверке и сохранении; перед отправкой потребуется новый расчёт.',
                'DNS-проверка не подтверждает существование ящика или согласие на рекламу.']
    if not counts['ready']: warnings.append('После исключений нет получателей. Черновик можно сохранить.')
    if checks['errors']: warnings.append('В шаблоне есть ошибки. Исправьте их перед отправкой.')
    return {'name':name.strip(), 'audience_ids':ids, 'template':template_data, 'sender':sender_record(sender),
            'schedule_mode':mode, 'scheduled_at':scheduled, 'schedule_timezone':zone,
            'counts':counts, 'checks':checks, 'warnings':warnings}


@require_POST
def recipient_preview(request):
    try: return JsonResponse(recipients(body(request)))
    except ValueError as exc: return error(str(exc))


@require_POST
def preview(request):
    try: return JsonResponse(plan(body(request)))
    except (ValueError,TypeError) as exc: return error(str(exc))


def record(row, full=False):
    value = {k:getattr(row,k) for k in ('id','name','revision','schedule_mode','scheduled_at','schedule_timezone','updated_at')}
    queue=getattr(row,'dispatch_queue',None)
    value.update(status='draft', recipient_counts=row.recipient_counts,queue={'id':queue.pk,'state':queue.state,'mode':'simulation'} if queue else None)
    from .campaign_approval import record as approval_record
    value['approval']={k:v for k,v in approval_record(row).items() if k!='snapshot'}
    if full:
        value.update(audience_ids=list(row.audiences.values_list('id',flat=True)),template_id=row.template_id,
                     sender_id=row.sender_id,template_snapshot=row.template_snapshot,sender_snapshot=row.sender_snapshot)
    return value


@require_http_methods(['GET','POST'])
def campaigns(request, campaign_id=None):
    if request.method == 'GET':
        if campaign_id:
            row = EmailCampaign.objects.select_related('dispatch_queue').filter(pk=campaign_id).first()
            return JsonResponse(record(row,True)) if row else error('Рассылка не найдена.',404)
        rows = EmailCampaign.objects.select_related('dispatch_queue').order_by('-updated_at','-id')
        return JsonResponse({'campaigns':[record(row) for row in rows[:200]],'total':rows.count()})
    data = body(request)
    try:
        with transaction.atomic():
            row = None
            if campaign_id:
                row = EmailCampaign.objects.select_for_update().filter(pk=campaign_id).first()
                if not row: return error('Рассылка не найдена.',404)
                if CampaignQueue.objects.filter(campaign=row).exists():
                    return error('Для рассылки уже подготовлена очередь. Создайте новую рассылку для другого состава или письма.',409)
                if type(data.get('revision')) is not int or data['revision'] != row.revision:
                    return error('Черновик изменён в другом окне. Откройте актуальную версию.',409)
            before=audit.campaign_snapshot(row)
            values = plan(data,lock=True)
            row = row or EmailCampaign()
            for field in ('name','schedule_mode','scheduled_at','schedule_timezone'): setattr(row,field,values[field])
            row.template_id=values['template']['id'];row.sender_id=values['sender']['id']
            row.template_snapshot=values['template'];row.sender_snapshot=values['sender'];row.recipient_counts=values['counts']
            if row.pk: row.revision += 1
            row.save();row.audiences.set(values['audience_ids'])
            if campaign_id:
                from .campaign_approval import invalidate
                invalidate(row,'Черновик изменён: письмо, аудитории, отправитель или расписание требуют повторного согласования.')
            audit.record('updated' if campaign_id else 'created','campaign',row.pk,row.name,campaigns=[row.pk],
                before=before,after=audit.campaign_snapshot(row),summary='Черновик рассылки сохранён. Отправка не выполнялась.')
        return JsonResponse(record(row,True),status=200 if campaign_id else 201)
    except (ValueError,TypeError) as exc: return error(str(exc))

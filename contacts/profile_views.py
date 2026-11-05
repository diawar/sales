import uuid
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from .models import Client,Contact,Audience
from .profiles import validate_profile,apply_profile,attach_contacts,serialize
from .views import body,error,contact_records

@require_GET
def detail(request, contact_id):
    try:
        contact=Contact.objects.select_related('client').get(pk=contact_id)
    except Contact.DoesNotExist:
        return error('Контакт не найден.',404)
    record=next(contact_records(Contact.objects.filter(pk=contact_id)))
    return JsonResponse({**record,**serialize(contact.client)})

def contacts_input(value):
    emails=value.get('emails',[]);phones=value.get('phones',[])
    if not isinstance(emails,list) or len(emails)>30 or any(not isinstance(v,str) for v in emails):raise ValueError('Укажите email списком, не более 30.')
    if not isinstance(phones,list) or len(phones)>30 or any(not isinstance(v,str) or len(v)>200 for v in phones):raise ValueError('Укажите телефоны списком, не более 30.')
    emails=list(dict.fromkeys(v.strip().casefold() for v in emails if v.strip()))
    for email in emails:
        try:validate_email(email)
        except ValidationError:raise ValueError('Проверьте email клиента.')
    return emails,[v.strip() for v in phones if v.strip()]

@require_POST
@transaction.atomic
def save(request,contact_id=None):
    payload=body(request)
    try:
        values=validate_profile(payload)
        emails,phones=contacts_input(payload)
        if 'notes' in payload and (not isinstance(payload['notes'],str) or len(payload['notes'])>4000):raise ValueError('Заметка не более 4000 символов.')
        if contact_id:
            try:contact=Contact.objects.select_related('client').get(pk=contact_id)
            except Contact.DoesNotExist:return error('Контакт не найден.',404)
            client=Client.objects.select_for_update().get(pk=contact.client_id)
            audiences=list(contact.audiences.all())
        else:
            if not values.get('name'):raise ValueError('Укажите имя или название клиента.')
            try:audience=Audience.objects.get(pk=payload.get('audience_id'))
            except (Audience.DoesNotExist,ValueError,TypeError):raise ValueError('Выберите аудиторию.')
            identifier=str(payload.get('identifier','')).strip()
            if identifier and (len(identifier)!=12 or not identifier.isascii() or not identifier.isdigit()):raise ValueError('БИН: 12 цифр, либо оставьте поле пустым.')
            root=audience.parent or audience
            if root.name=='Физические лица' and identifier:raise ValueError('ИИН физических лиц в этой карточке не собираем.')
            if identifier:
                existing=Client.objects.filter(identity_key='kz:'+identifier).first()
                if existing:return error('Клиент с этим идентификатором уже существует. Откройте его карточку.',409)
            client=Client.objects.create(identity_key='kz:'+identifier if identifier else 'manual:'+uuid.uuid4().hex,name=values['name'],identifier=identifier)
            audiences=[audience]
        apply_profile(client,values,'Вручную',manual=True)
        if 'notes' in payload:
            client.notes=payload['notes'];client.save(update_fields=['notes'])
        if contact_id and 'emails' not in payload and 'phones' not in payload:
            contacts=list(Contact.objects.filter(client=client))
        else:contacts=attach_contacts(client,emails,phones,audiences,'Вручную')
        return JsonResponse({'saved':True,'contact_id':contacts[0].id,**serialize(client)})
    except ValueError as exc:
        transaction.set_rollback(True)
        return error(str(exc))

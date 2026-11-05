"""Validated client fields, enrichment and preserved provenance."""
import hashlib
import json
import re
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator,validate_email
from django.db import transaction
from .models import ClientFact,Address,Contact,Evidence,Phone
from .phones import parse_phone

SCALARS={'name':300,'city':160,'industry':200,'organization_type':200}
LISTS={'websites','socials','contact_people','tags'}

def url(value):
    URLValidator(schemes=['http','https'])(value)
    if len(value)>1000:raise ValueError('Ссылка не более 1000 символов.')
    return value

def validate_profile(payload):
    result={}
    try:
        for key,limit in SCALARS.items():
            if key in payload:
                value=payload[key]
                if not isinstance(value,str) or len(value.strip())>limit:raise ValueError(f'Некорректное поле {key}, максимум {limit} символов.')
                if key=='name' and not value.strip():raise ValueError('Укажите имя или название клиента.')
                result[key]=value.strip()
        for key in LISTS:
            if key not in payload:continue
            values=payload[key]
            if not isinstance(values,list) or len(values)>30:raise ValueError('Не более 30 значений одного вида.')
            if key=='tags':
                normalized=[];seen=set()
                for value in values:
                    if not isinstance(value,str):raise ValueError('Теги должны быть текстом.')
                    for tag in re.split(r'[\s|]+',value):
                        tag=tag.lstrip('#')
                        if tag and tag.casefold() not in seen:
                            seen.add(tag.casefold());normalized.append(tag)
                values=normalized
                if len(values)>30:raise ValueError('Не более 30 тегов.')
            clean=[]
            for item in values:
                if key in {'tags','websites'}:
                    if not isinstance(item,str) or not item.strip() or len(item)>1000:raise ValueError('Некорректное значение списка.')
                    item=item.strip()
                    if key=='websites':url(item)
                    elif len(item)>80:raise ValueError('Тег не более 80 символов.')
                elif key=='socials':
                    if not isinstance(item,dict):raise ValueError('Для соцсети нужны сервис и ссылка/username.')
                    service=item.get('service','');value=item.get('value','')
                    if not isinstance(service,str) or not isinstance(value,str) or not service.strip() or not value.strip() or len(service)>60 or len(value)>1000:raise ValueError('Для соцсети нужны сервис и ссылка/username.')
                    if value.startswith(('http:','https:')):url(value)
                    elif ':' in value or any(c in value for c in '<>\n\r'):raise ValueError('Недопустимая ссылка соцсети.')
                    item={'service':service.strip(),'value':value.strip()}
                else:
                    if not isinstance(item,dict):raise ValueError('Некорректное контактное лицо.')
                    item={k:item.get(k,'') for k in ['name','role','email','phone']}
                    if any(not isinstance(v,str) or len(v)>200 for v in item.values()) or not item['name'].strip():raise ValueError('Укажите имя контактного лица; поля не более 200 символов.')
                    item={k:v.strip() for k,v in item.items()}
                    if item['email']:validate_email(item['email'])
                if item not in clean:clean.append(item)
            result[key]=clean
    except ValidationError:
        raise ValueError('Проверьте адрес сайта, ссылку или email контактного лица.')
    return result

def fact(client,key,value,source,source_url,state):
    digest=hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    entry,_=ClientFact.objects.update_or_create(client=client,field=key,fingerprint=digest,source=source,source_url=source_url,defaults={'value':value,'state':state})
    return entry

@transaction.atomic
def apply_profile(client,values,source,source_url='',manual=False):
    conflicts=[]
    for key,incoming in values.items():
        current=getattr(client,key)
        if manual and current and current!=incoming and not client.facts.filter(field=key).exists():
            for previous in (current if key in LISTS else [current]):
                fact(client,key,previous,'Ранее сохранено','','superseded')
        if key in LISTS:
            merged=incoming if manual else current+[v for v in incoming if v not in current]
            if key=='tags':
                seen=set();merged=[v for v in merged if not (v.casefold() in seen or seen.add(v.casefold()))]
            if manual:client.facts.filter(field=key,state='accepted').update(state='superseded')
            for value in incoming:fact(client,key,value,source,source_url,'accepted')
            setattr(client,key,merged)
        else:
            if not manual and not incoming:continue
            conflict=bool(current and current!=incoming and not manual)
            if conflict:
                fact(client,key,incoming,source,source_url,'alternative');conflicts.append(key)
            else:
                if manual:client.facts.filter(field=key,state='accepted').update(state='superseded')
                fact(client,key,incoming,source,source_url,'accepted');setattr(client,key,incoming)
    if values:client.save(update_fields=list(values))
    return conflicts

@transaction.atomic
def attach_contacts(client,emails,phones,audiences,source,source_url=''):
    contacts=[]
    for email in emails:
        address,_=Address.objects.get_or_create(email=email)
        contact=Contact.objects.filter(client=client,address=None).first()
        if contact:
            contact.address=address;contact.save(update_fields=['address'])
        else:contact,_=Contact.objects.get_or_create(client=client,address=address)
        contacts.append(contact)
    if not contacts:
        contacts=list(Contact.objects.filter(client=client))
        if not contacts:
            contact,_=Contact.objects.get_or_create(client=client,address=None);contacts=[contact]
    for contact in contacts:
        contact.audiences.add(*audiences)
        Evidence.objects.update_or_create(contact=contact,source=source,source_url=source_url,defaults={'current':True})
    for raw in phones:
        phone=parse_phone(raw)
        Phone.objects.update_or_create(client=client,number=phone['number'],source=source,source_url=source_url,defaults={'raw':phone['raw'],'kind':phone['kind']})
    return contacts

def serialize(client, *, detail=True):
    contacts=list(client.contact_set.all())
    result={**{key:getattr(client,key) for key in SCALARS},**{key:getattr(client,key) for key in LISTS},'identifier':client.identifier,'client_id':client.id,'all_emails':[c.address.email for c in contacts if c.address_id]}
    if detail:
        history=client.facts.order_by('-last_seen','-id')[:100]
        result.update(contact_sources=[{'email':c.address.email if c.address_id else '', 'evidence':[{'source':e.source,'url':e.source_url,'current':e.current} for e in c.evidence.all()]} for c in contacts],field_history=[{key:getattr(f,key) for key in ['field','value','source','source_url','state','first_seen','last_seen']} for f in history])
    return result

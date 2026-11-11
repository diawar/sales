import csv
import io
import json
import uuid
import hashlib
from datetime import timedelta
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email, URLValidator
from django.db import transaction, IntegrityError
from django.db.models import Prefetch, Count, Q, Exists, OuterRef
from django.http import JsonResponse, HttpResponse, FileResponse, Http404, StreamingHttpResponse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods, require_POST
from .models import Audience, Client, Address, Contact, Evidence, ImportBatch, Phone
from .import_files import read_table
from .phones import parse_phone
from .profiles import serialize,apply_profile
from .profile_import import ALIASES,parse_extra,identity

def error(message, status=400):
    return JsonResponse({'error': message}, status=status)

def body(request):
    try:
        value = json.loads(request.body)
        if not isinstance(value, dict):
            return {}
        return value
    except (ValueError, UnicodeDecodeError):
        return {}

@ensure_csrf_cookie
def static_file(request, filename='index.html'):
    if filename == 'sample.csv':
        return FileResponse((settings.BASE_DIR / 'fixtures' / 'contacts.example.csv').open('rb'), as_attachment=True, filename='contacts.example.csv')
    if filename not in {'index.html','app.js','details.js','database.js','navigation.js','phones-ui.js','tags.js','profile-ui.js','collection.js','eondiris.js','source-layout.js','gov.js','dfo.js','overshop.js','kaspi.js','goszakup.css','match.css','details.css','icons.svg','favicon.svg'}:
        raise Http404
    path = settings.BASE_DIR / 'prototype' / 'dist' / filename
    return FileResponse(path.open('rb'))

@ensure_csrf_cookie
def contacts(request):
    return contacts_response(Contact.objects.all())

@ensure_csrf_cookie
@require_http_methods(['GET'])
def contacts_page(request):
    from .contact_query import contact_page
    try:
        rows, pagination = contact_page(request.GET)
    except ValueError as exc:
        return error(str(exc))
    return contacts_response(rows, pagination)

def contacts_response(queryset, pagination=None):
    payload={'contacts':list(contact_records(queryset)),'audiences':audience_rows()}
    if pagination is not None:
        payload['pagination']=pagination
    return JsonResponse(payload,
                        json_dumps_params={'ensure_ascii':False,'separators':(',',':')})

def contact_records(queryset):
    audience_lookup={a.id:a.name for a in Audience.objects.all()}
    # Join nullable addresses instead of generating a large OR expression for
    # their forward prefetch (SQLite limits expression depth to 1000).
    client_contacts = Contact.objects.select_related('address')
    rows = queryset.select_related('client','address').prefetch_related(
        'audiences','evidence','client__phones',
        Prefetch('client__contact_set', queryset=client_contacts),
    )
    if not rows.ordered:
        rows=rows.order_by('id')
    for contact in rows.iterator(chunk_size=200):
        memberships=list(contact.audiences.all())
        audience_ids={a.id for a in memberships}
        audience_ids.update(a.parent_id for a in memberships if a.parent_id)
        audiences = sorted(audience_lookup[pk] for pk in audience_ids)
        evidence = list(contact.evidence.all())
        address=contact.address
        excluded=bool(address and (address.archived or address.suppression_reason))
        row={'id':contact.id,'name':contact.client.name,'identifier':contact.client.identifier,'email':address.email if address else '', 'audience':audiences[0] if audiences else '', 'audiences':audiences,'source':evidence[0].source if evidence else '', 'sources':sorted({e.source for e in evidence}), 'evidence':[{'source':e.source,'url':e.source_url,'current':e.current,'last_seen':e.last_seen} for e in evidence], 'validation':address.validation if address else 'Без email','archived':address.archived if address else False,'activity':'Исключены' if excluded else 'Мало данных','delivered':0,'opens':0,'clicks':0,'notes':contact.client.notes,'publication':'Опубликован' if any(e.current for e in evidence) else 'Исторический контакт'}
        row['audience_ids']=sorted(audience_ids)
        row['validation_checked_at']=address.validation_checked_at if address else None
        row['validation_result']=address.validation_result if address else {}
        row['phones']=[{'number':p.raw if p.kind=='unknown' else p.number,'kind':p.kind,'source':p.source,'url':p.source_url} for p in contact.client.phones.all()]
        row.update(serialize(contact.client, detail=False))
        yield row

@require_http_methods(['GET'])
def contacts_summary(request):
    coverage_rows=Client.objects.annotate(
        has_email=Exists(Contact.objects.filter(client_id=OuterRef('pk'),address__isnull=False)),
        has_phone=Exists(Phone.objects.filter(client_id=OuterRef('pk'))))
    coverage=coverage_rows.aggregate(clients=Count('id'),
        with_email=Count('id',filter=Q(has_email=True)),
        with_phone=Count('id',filter=Q(has_phone=True)),
        missing_both=Count('id',filter=Q(has_email=False,has_phone=False)))
    excluded=Q(address__archived=True)|(Q(address__isnull=False)&~Q(address__suppression_reason=''))
    aggregate=Contact.objects.aggregate(total=Count('id'),working=Count('id',filter=~Q(address__archived=True)),
        attention=Count('id',filter=Q(address__validation__in=['Не проверен','Временная ошибка'])))
    memberships={}
    parents=dict(Audience.objects.values_list('id','parent_id'))
    # Count each contact once in its root even if it belongs to several children.
    for row in Contact.objects.values('id','address__archived','address__suppression_reason','audiences__id').iterator(chunk_size=1000):
        aid=row['audiences__id']
        if aid is None:continue
        blocked=bool(row['address__archived'] or row['address__suppression_reason'])
        for target in {aid,parents.get(aid)}-{None}:
            members=memberships.setdefault(target,{})
            members[row['id']]=(bool(row['address__archived']),blocked)
    counts={str(aid):{'total':len(members),'working':sum(not v[0] for v in members.values()),
        'segments':{'Активные':0,'Пассивные':0,'Мало данных':sum(not v[1] for v in members.values()),'Исключены':sum(v[1] for v in members.values())}}
        for aid,members in memberships.items()}
    return JsonResponse({'stats':{**aggregate,'active':0,'passive':0},'coverage':coverage,'audience_counts':counts,
        'sources':list(Evidence.objects.order_by('source').values_list('source',flat=True).distinct())})

@require_http_methods(['GET'])
def contacts_export(request):
    from .contact_query import filtered_contacts
    try:rows=filtered_contacts(request.GET)
    except ValueError as exc:return error(str(exc))
    class Echo:
        def write(self,value):return value
    writer=csv.writer(Echo(),delimiter=';',quoting=csv.QUOTE_ALL)
    def safe(value):
        text=str(value)
        return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text
    def stream():
        yield '\ufeff'
        yield writer.writerow(['Компания','Email','Аудитория','Активность','Проверка','Доставлено','Открытия','Переходы','Источник','Телефоны','БИН','Город','Отрасль','Тип организации','Сайт','Соцсети','Теги'])
        for row in contact_records(rows):
            values=[row['name'],row['email'],', '.join(row['audiences']),row['activity'],row['validation'],0,0,0,row['source'],
                ' | '.join(p['number'] for p in row['phones']),row['identifier'],row['city'],row['industry'],row['organization_type'],
                ' | '.join(row['websites']),' | '.join(s['service']+'='+s['value'] for s in row['socials']),
                ' '.join('#'+t for t in row['tags'])]
            yield writer.writerow([safe(v) for v in values])
    response=StreamingHttpResponse(stream(),content_type='text/csv; charset=utf-8')
    response['Content-Disposition']='attachment; filename="contacts.csv"'
    return response

def audience_rows():
    return [{'id':a.id,'name':a.name,'parent_id':a.parent_id,'label':f'{a.parent.name} → {a.name}' if a.parent_id else a.name} for a in Audience.objects.select_related('parent').order_by('name')]

@require_http_methods(['GET','POST'])
def audiences(request):
    if request.method == 'GET':
        return JsonResponse({'audiences':audience_rows()})
    name = str(body(request).get('name','')).strip()
    if not name or len(name)>120:
        return error('Название аудитории: от 1 до 120 символов.')
    try:
        parent=Audience.objects.get(pk=body(request).get('parent_id'),parent__isnull=True)
    except (Audience.DoesNotExist,ValueError,TypeError):
        return error('Выберите основную группу.')
    with transaction.atomic():
        if Audience.objects.filter(name_key=str(parent.id)+':'+name.casefold()).exists():
            return error('Такая аудитория уже существует.')
        audience=Audience.objects.create(name=name,parent=parent)
    return JsonResponse({'id':audience.id,'name':audience.name},status=201)

@require_POST
def note(request, contact_id):
    value=body(request).get('notes')
    if not isinstance(value,str) or len(value)>4000:
        return error('Заметка должна содержать не более 4000 символов.')
    try:
        contact=Contact.objects.get(pk=contact_id)
    except Contact.DoesNotExist:
        return error('Контакт не найден.',404)
    Client.objects.filter(pk=contact.client_id).update(notes=value)
    return JsonResponse({'saved':True})

@require_POST
def preview(request):
    file=request.FILES.get('file')
    if file is None or file.size>2*1024*1024:
        return error('Нужен CSV, XLS или XLSX размером до 2 МБ.')
    try:
        audience=Audience.objects.get(pk=request.POST.get('audience'))
    except (Audience.DoesNotExist,ValueError,TypeError):
        return error('Выберите аудиторию.')
    try:
        text=read_table(file)
    except ValueError as exc:
        return error(str(exc))
    aliases={'компания':'name','клиент':'name','name':'name','email':'email','e-mail':'email','почта':'email','источник':'source','source':'source','url':'url','source_url':'url','ссылка':'url','телефон':'phone','телефоны':'phone','phone':'phone'}
    aliases.update(ALIASES)
    rows=[]; errors=[]; seen=set(); duplicates=0
    try:
        delimiter=';' if text.splitlines()[0].count(';')>=text.splitlines()[0].count(',') else ','
        reader=csv.DictReader(io.StringIO(text),delimiter=delimiter,strict=True)
        if not reader.fieldnames:
            return error('CSV пуст.')
        fields=[aliases.get(str(k).strip().casefold(), str(k).strip()) for k in reader.fieldnames]
        if len(fields)!=len(set(fields)) or 'name' not in fields:
            return error('Нужна колонка Компания (или name); названия колонок не должны повторяться.')
        for number,row in enumerate(reader,2):
            if number>10001:
                return error('Не более 10 000 строк за импорт.')
            if None in row or any(v is None for v in row.values()):
                errors.append({'line':number,'error':'Количество значений не совпадает с заголовком.'});continue
            item={aliases.get(k.strip().casefold(), k.strip()):v.strip() for k,v in row.items()}
            name=item['name']; email=item.get('email','').casefold()
            source=item.get('source') or ('CSV' if file.name.lower().endswith('.csv') else 'Excel'); url=item.get('url','')
            phone=item.get('phone','')
            try:
                if email:validate_email(email)
                if not name or len(name)>300 or len(email)>254 or len(source)>80 or len(url)>1000 or len(phone)>200:
                    raise ValidationError('Длина поля или пустое название клиента.')
                if url:
                    URLValidator(schemes=['https','http'])(url)
                identifier,profile=parse_extra(item,(audience.parent or audience).name)
            except (ValidationError,ValueError) as exc:
                errors.append({'line':number,'error':str(exc) if isinstance(exc,ValueError) else 'Некорректный email, название, источник или URL.'});continue
            key=(name.casefold(),email,source,url,phone,identifier,json.dumps(profile,sort_keys=True))
            if key in seen:
                duplicates+=1;continue
            seen.add(key);rows.append({'name':name,'email':email,'source':source,'url':url,'identifier':identifier,'identity_key':identity(name,email,identifier),'profile':profile,'phones':[parse_phone(p.strip()) for p in phone.split('|') if p.strip()]})
    except (csv.Error,IndexError):
        return error('Не удалось прочитать CSV. Проверьте разделители и кавычки.')
    existing=Contact.objects.select_related('client','address')
    keys={(c.client.identity_key,c.address.email if c.address_id else '') for c in existing}
    stats={'valid':len(rows),'errors':len(errors),'duplicates':duplicates,'new_contacts':len({(r['identity_key'],r['email']) for r in rows}-keys),'unidentified':sum(not r['email'] and not r['identifier'] for r in rows)}
    batch=ImportBatch.objects.create(token=uuid.uuid4(),audience=audience,rows=rows,errors=errors)
    return JsonResponse({'token':str(batch.token),'stats':stats,'errors':errors[:100],'preview':rows[:20],'can_commit':bool(rows) and not errors})

@require_POST
@transaction.atomic
def commit(request):
    try:
        batch=ImportBatch.objects.select_for_update().get(token=body(request).get('token'))
    except (ImportBatch.DoesNotExist,ValueError,ValidationError):
        return error('Предпросмотр не найден.',404)
    if batch.committed_at:
        return JsonResponse(batch.result)
    if batch.created_at<timezone.now()-timedelta(hours=24):
        return error('Предпросмотр устарел. Загрузите файл ещё раз.')
    if batch.errors or not batch.rows:
        return error('Исправьте ошибки и повторите загрузку. Пакет не записан.')
    created=0; updated=0
    for row in batch.rows:
        # Without a business identifier, do not merge different addresses by name alone.
        identity_key=row.get('identity_key') or identity(row['name'],row['email'],row.get('identifier',''))
        client,_=Client.objects.get_or_create(identity_key=identity_key,defaults={'name':row['name'],'identifier':row.get('identifier','')})
        apply_profile(client,{'name':row['name'],**row.get('profile',{})},row['source'],row['url'])
        address=Address.objects.get_or_create(email=row['email'])[0] if row['email'] else None
        placeholder=Contact.objects.filter(client=client,address=None).first() if address else None
        if placeholder and not Contact.objects.filter(client=client,address=address).exists():
            placeholder.address=address;placeholder.save(update_fields=['address'])
        contact,is_new=Contact.objects.get_or_create(client=client,address=address)
        created+=int(is_new);updated+=int(not is_new)
        contact.audiences.add(batch.audience)
        Evidence.objects.update_or_create(contact=contact,source=row['source'],source_url=row['url'])
        for phone in row.get('phones',[]):
            Phone.objects.update_or_create(client=client,number=phone['number'],source=row['source'],source_url=row['url'],defaults={'raw':phone['raw'],'kind':phone['kind']})
    batch.committed_at=timezone.now();batch.result={'created':created,'updated':updated,'committed':True};batch.save(update_fields=['committed_at','result'])
    return JsonResponse(batch.result)

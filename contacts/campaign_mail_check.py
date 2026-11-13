"""Campaign-scoped, no-send preparation using saved snapshots only."""
import json
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from .models import EmailCampaign, Contact, Audience
from .campaign_views import audience_selection
from .campaign_readiness import url_issue
from .mail_tracking import public_origin, prepare_preview
from .mail_integration_views import private_response
from .template_tools import TOKEN, personalize, check_template
from .views import body, error


def snapshots(campaign):
    queue=getattr(campaign,'dispatch_queue',None)
    template=queue.snapshot.get('template',{}) if queue else campaign.template_snapshot
    sender=queue.snapshot.get('sender',{}) if queue else campaign.sender_snapshot
    if not isinstance(template,dict) or not all(isinstance(template.get(k),str) for k in ('subject','preheader','html')):
        raise ValueError('В рассылке нет корректной сохранённой копии письма.')
    if not isinstance(sender,dict):raise ValueError('В рассылке нет корректной копии отправителя.')
    version={'campaign_revision':campaign.revision,'queue_id':queue.pk if queue else None,
             'queue_revision':queue.snapshot.get('campaign_revision') if queue else None}
    return template,sender,version,'queue' if queue else 'campaign'


def scoped_contacts(campaign):
    ids=list(campaign.audiences.values_list('pk',flat=True))
    expanded=audience_selection({'audience_ids':ids})[1] if ids else []
    rows=Contact.objects.filter(audiences__pk__in=expanded).select_related('client','address').prefetch_related('audiences').distinct()
    queue=getattr(campaign,'dispatch_queue',None)
    if queue:rows=rows.filter(address_id__in=queue.items.values('address_id'))
    return rows


def contact_values(contact,parents):
    physical=False
    for audience in contact.audiences.all():
        aid=audience.pk;seen=set()
        while aid in parents and aid not in seen:
            seen.add(aid);name,parent=parents[aid]
            if name=='Физические лица':physical=True
            aid=parent
    return {'id':contact.pk,'client_id':contact.client_id,'label':contact.client.name,
            'email':contact.address.email if contact.address else '',
            'values':{'name':contact.client.name,'company':'' if physical else contact.client.name,'city':contact.client.city}}


def diagnosis(request,template,sender,recipient,values):
    if not isinstance(recipient,str) or len(recipient)>254:raise ValueError('Проверьте email для проверки.')
    recipient=recipient.strip().casefold()
    if not isinstance(values,dict) or any(k not in {'name','company','city'} or not isinstance(v,str) or len(v)>250 for k,v in values.items()):
        raise ValueError('Проверьте персонализацию (до 250 символов в поле).')
    context={**values,'email':recipient};checks=[]
    def add(code,status,title,detail):checks.append({'code':code,'status':status,'title':title,'detail':detail})
    try:validate_email(recipient);email_ok=True
    except ValidationError:email_ok=False
    add('recipient','pass' if email_ok else 'fail','Адрес для проверки','Адрес имеет допустимый формат; письмо не отправляется.' if email_ok else 'Нужен корректный email.')
    missing=set()
    for source in (template['subject'],template['preheader'],template['html']):
        for match in TOKEN.finditer(source):
            name,fallback=match.groups()
            if name in ('name','company','city','email') and not context.get(name,'').strip() and not (fallback or '').strip():missing.add(name)
    add('personalization','fail' if missing else 'pass','Персонализация','Нет значений или запасного текста: '+', '.join(sorted(missing)) if missing else 'Все использованные переменные заполнены или имеют запасной текст.')
    check=check_template(template['subject'],template['preheader'],template['html'],context)
    add('template','fail' if check['errors'] else 'warning' if check['warnings'] else 'pass','Сохранённый шаблон',f'Ошибок: {check["errors"]}; замечаний: {check["warnings"]}.')
    rendered,issues=personalize(template['html'],context,True)
    subject,subject_issues=personalize(template['subject'],context)
    subject_ok=bool(subject.strip()) and len(subject)<=250 and not any(c in subject for c in '\r\n')
    add('subject','pass' if subject_ok else 'fail','Тема после подстановки','Допустимая тема.' if subject_ok else 'Нужна непустая тема до 250 символов без переносов строк.')
    problems=[]
    for tag in BeautifulSoup(rendered,'html.parser').find_all(True):
        for attr in ('href','src'):
            if attr in tag.attrs and url_issue(str(tag[attr]),attr):problems.append(str(tag[attr])[:200])
    add('links','fail' if problems else 'pass','Ссылки после подстановки',f'Некорректных адресов: {len(problems)}. Внешние сайты не посещаются.')
    sender_bad=not isinstance(sender.get('name'),str) or not sender.get('name','').strip()
    for key in ('name','email','reply_to'):
        value=sender.get(key,'')
        if not isinstance(value,str) or len(value)>254 or any(c in value for c in '\r\n'):sender_bad=True;continue
        if key=='name' or key=='reply_to' and not value:continue
        try:validate_email(value)
        except ValidationError:sender_bad=True
    add('sender','fail' if sender_bad else 'pass','Сохранённый отправитель','Проверьте имя, email и адрес для ответов.' if sender_bad else 'Реквизиты письма имеют допустимый формат; владение доменом не подтверждено.')
    try:
        origin=public_origin(request)
        local=urlsplit(origin).hostname in ('localhost','127.0.0.1','testserver')
        add('origin','warning' if local else 'pass','Адрес учёта событий',origin+(' — доступен только локально.' if local else ' — HTTPS-адрес задан.'))
        add('availability','pending','Публичная доступность','Доступность пикселя, переходов и страницы отписки из интернета не проверялась. Потребуется проверка после развёртывания.')
    except ValueError as exc:add('origin','fail','Адрес учёта событий',str(exc))
    add('unsubscribe','fail' if any(f['code']=='unsubscribe' for f in check['findings']) else 'pass','Подстановка отписки','При подготовке создаётся ссылка тестовой подписки. Открытие страницы безопасно, отписка применяется по кнопке.')
    add('transport','pending','Отправка отключена','Проверка не использует SMTP, не подтверждает доставку и не меняет рабочие подписки.')
    return {'can_prepare':not any(c['status']=='fail' for c in checks),'can_send':False,'checks':checks,
            'missing':sorted(missing),'findings':check['findings'],'recipient':recipient,'values':values}


@require_http_methods(['GET','POST'])
@transaction.atomic
def mail_check(request,campaign_id):
    rows=EmailCampaign.objects.select_related('template','dispatch_queue')
    if request.method=='POST':rows=rows.select_for_update(of=('self',))
    campaign=rows.filter(pk=campaign_id).first()
    if not campaign:return error('Рассылка не найдена.',404)
    try:
        template,sender,version,basis=snapshots(campaign)
        if request.method=='GET':
            query=request.GET.get('query','').strip()
            if len(query)>160:raise ValueError('Поиск клиента: до 160 символов.')
            contacts=scoped_contacts(campaign)
            if query:contacts=contacts.filter(Q(client__name__icontains=query)|Q(address__email__icontains=query))
            sample=list(contacts.order_by('client__name','pk')[:21])
            parents={pk:(name,parent) for pk,name,parent in Audience.objects.values_list('pk','name','parent_id')}
            return private_response(JsonResponse({'campaign_id':campaign.pk,'campaign_name':campaign.name,
                'version':version,'basis':basis,'sender':sender,'template':{k:template.get(k) for k in ('id','name','revision','subject','preheader')},
                'clients':[contact_values(c,parents) for c in sample[:20]],'clients_more':len(sample)>20,
                'note':'Проверяется копия из очереди.' if basis=='queue' else 'Проверяется сохранённая копия рассылки.',
                'can_send':False}))
        data=body(request)
        if data.get('is_test',True) is not True or data.get('send',False) is not False:
            return error('Доступна только проверка без отправки.')
        if json.dumps(data.get('version'),sort_keys=True)!=json.dumps(version,sort_keys=True):
            return error('Рассылка или очередь изменилась. Обновите проверку и подготовьте письмо заново.',409)
        action=data.get('action','diagnose')
        if action not in ('diagnose','prepare'):raise ValueError('Выберите проверку или подготовку письма.')
        contact_id=data.get('contact_id')
        if contact_id is not None:
            if type(contact_id) is not int or not scoped_contacts(campaign).filter(pk=contact_id).exists():
                raise ValueError('Клиент не входит в получателей этой рассылки.')
        report=diagnosis(request,template,sender,data.get('recipient',''),data.get('values',{}))
        if action=='diagnose':return private_response(JsonResponse(report))
        if not report['can_prepare']:return private_response(JsonResponse({'error':'Исправьте ошибки проверки.','diagnosis':report},status=400))
        result=prepare_preview(request,campaign.template,report['recipient'],report['values'],campaign,
            template_snapshot=template,sender_snapshot=sender,
            metadata={'version':version,'basis':basis,'contact_id':contact_id})
        return private_response(JsonResponse(result|{'diagnosis':report},status=201))
    except ValueError as exc:return error(str(exc))

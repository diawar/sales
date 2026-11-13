import json
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from .models import EmailTemplate
from .views import error
from .template_design import validate_design
from .template_tools import check_template
from . import template_ai
from .models import TemplateAIQuota
from django.utils import timezone
from datetime import timedelta


def record(row, full=False):
    value={key:getattr(row,key) for key in ('id','name','subject','preheader','revision','updated_at')}
    if full:
        value['html']=row.html
        value['design']=row.design
    return value


@require_http_methods(['GET','POST'])
def templates(request, template_id=None):
    if request.method=='GET':
        rows=EmailTemplate.objects.all()
        if template_id:
            row=rows.filter(pk=template_id).first()
            return JsonResponse(record(row,True)) if row else error('Шаблон не найден.',404)
        query=request.GET.get('q','').strip()[:160]
        rows=rows.filter(name__icontains=query).order_by('-updated_at','-id')
        return JsonResponse({'templates':[record(row) for row in rows[:200]],'total':rows.count()})
    try:
        data=json.loads(request.body)
        if not isinstance(data,dict):raise ValueError()
        values={}
        for key,limit in [('name',160),('subject',250),('preheader',250),('html',500000)]:
            value=data.get(key,'')
            if not isinstance(value,str) or len(value)>limit:raise ValueError()
            values[key]=value.strip() if key!='html' else value
        if not values['name']:return error('Укажите название шаблона.')
        values['design']=validate_design(data.get('design'))
    except (ValueError,UnicodeDecodeError):return error('Некорректные данные или превышен лимит поля (HTML до 500 000 символов).')
    with transaction.atomic():
        if template_id:
            row=EmailTemplate.objects.select_for_update().filter(pk=template_id).first()
            if not row:return error('Шаблон не найден.',404)
            if data.get('revision')!=row.revision:return error('Шаблон изменён в другом окне. Откройте его заново; ваш текст остаётся в редакторе.',409)
            for key,value in values.items():setattr(row,key,value)
            row.revision+=1
            row.save()
            from .models import EmailCampaign
            from .campaign_approval import invalidate
            for campaign in EmailCampaign.objects.filter(template=row,approval__status__in=('pending','approved')):
                invalidate(campaign,'Шаблон письма изменён в библиотеке. Откройте рассылку, сохраните актуальную версию и отправьте на проверку.')
        else:row=EmailTemplate.objects.create(**values)
    return JsonResponse(record(row,True),status=200 if template_id else 201)


def read_data(request):
    value=json.loads(request.body)
    if not isinstance(value,dict):raise ValueError()
    return value


@require_http_methods(['POST'])
def check(request):
    try:
        data=read_data(request)
        for key,limit in [('subject',250),('preheader',250),('html',500000)]:
            if not isinstance(data.get(key,''),str) or len(data.get(key,''))>limit:raise ValueError()
        values=data.get('values',{})
        if not isinstance(values,dict) or any(k not in {'name','company','city','email'} or not isinstance(v,str) or len(v)>250 for k,v in values.items()):raise ValueError()
    except (ValueError,UnicodeDecodeError):return error('Некорректные данные для проверки.')
    return JsonResponse(check_template(data.get('subject',''),data.get('preheader',''),data.get('html',''),values))


@require_http_methods(['GET','POST'])
def ai(request):
    if request.method=='GET':return JsonResponse({'configured':template_ai.configured(),'daily_limit':30,'note':'Для включения задайте OPENAI_API_KEY и SALES_AI_MODEL в окружении сервера и перезапустите приложение. Ключ не вводится в браузер.'})
    try:
        data=read_data(request);action=data.get('action')
        if action not in template_ai.ACTIONS:raise ValueError()
        for key,limit in [('instruction',3000),('source',8000)]:
            if not isinstance(data.get(key,''),str) or len(data.get(key,''))>limit:raise ValueError()
        if not (data.get('instruction','').strip() or data.get('source','').strip()):raise ValueError()
    except (ValueError,UnicodeDecodeError,TypeError):return error('Укажите задание (до 3000 символов) или исходный текст (до 8000).')
    if not template_ai.configured():return error('AI не настроен: нужны ключ API и модель в окружении сервера.',503)
    now=timezone.now()
    with transaction.atomic():
        quota,_=TemplateAIQuota.objects.get_or_create(pk=1)
        quota=TemplateAIQuota.objects.select_for_update().get(pk=quota.pk)
        if quota.day!=timezone.localdate():quota.day=timezone.localdate();quota.calls=0
        if quota.calls>=30:return error('Дневной лимит AI (30 запросов) исчерпан.',429)
        if quota.next_at and quota.next_at>now:return error('Подождите 10 секунд между запросами AI.',429)
        quota.calls+=1;quota.next_at=now+timedelta(seconds=10);quota.save()
    try:return JsonResponse({'suggestion':template_ai.generate(action,data.get('instruction',''),data.get('source',''))})
    except ValueError as exc:return error(str(exc),502)

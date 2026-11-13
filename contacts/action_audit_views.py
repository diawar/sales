from datetime import datetime,time,timedelta
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET,require_POST
from . import action_audit as audit
from .models import ActionAuditEntry
from .views import body,error
from .mail_integration_views import private_response


@require_GET
def journal(request):
    params=request.GET;rows=ActionAuditEntry.objects.all()
    try:
        query=params.get('query','').strip();actor=params.get('actor','');action=params.get('action','');kind=params.get('object_type','');result=params.get('result','')
        direction=params.get('direction','desc')
        if len(query)>180 or len(actor)>160 or action not in ('',*audit.ACTIONS) or kind not in ('',*audit.TYPES) or result not in ('',*audit.RESULTS) or direction not in ('asc','desc'):
            raise ValueError('Проверьте фильтры журнала действий.')
        if query:rows=rows.filter(Q(object_label__icontains=query)|Q(summary__icontains=query))
        if actor:rows=rows.filter(actor_key=actor)
        if action:rows=rows.filter(action=action)
        if kind:rows=rows.filter(object_type=kind)
        if result:rows=rows.filter(result=result)
        for key,field in (('campaign_id','campaign_refs__campaign_id'),('object_id','object_id')):
            value=params.get(key,'')
            if value:
                if not value.isascii() or not value.isdigit() or len(value)>18 or int(value)<1:raise ValueError('Проверьте ID объекта или рассылки.')
                rows=rows.filter(**{field:int(value)})
        dates={}
        for key in ('from','to'):
            value=params.get(key,'')
            if value:
                try:dates[key]=datetime.strptime(value,'%Y-%m-%d').date()
                except ValueError:raise ValueError('Дата: ГГГГ-ММ-ДД.') from None
        if 'from' in dates and 'to' in dates and dates['from']>dates['to']:raise ValueError('Начало периода позже окончания.')
        if 'from' in dates:rows=rows.filter(occurred_at__gte=timezone.make_aware(datetime.combine(dates['from'],time.min)))
        if 'to' in dates:rows=rows.filter(occurred_at__lt=timezone.make_aware(datetime.combine(dates['to']+timedelta(days=1),time.min)))
        page=int(params.get('page',1))
        if page<1:raise ValueError('Страница должна быть больше нуля.')
        prefix='-' if direction=='desc' else ''
        pages=Paginator(rows.order_by(prefix+'occurred_at',prefix+'pk').distinct(),25);current=pages.page(min(page,pages.num_pages))
        items=[]
        for r in current.object_list:
            items.append({'id':r.pk,'occurred_at':r.occurred_at,'actor':{'key':r.actor_key,'label':r.actor_label,'source':r.actor_source},
                'initiated_by':r.initiated_by,'action':r.action,'object_type':r.object_type,'object_id':r.object_id,'object_label':r.object_label,
                'result':r.result,'summary':r.summary,'before':r.before,'after':r.after,'request_id':r.request_id,
                'campaign_ids':list(r.campaign_refs.values_list('campaign_id',flat=True))})
        actors=list(ActionAuditEntry.objects.order_by('actor_key','-pk').values('actor_key','actor_label','actor_source'))
        seen=set();unique=[]
        for r in actors:
            if r['actor_key'] not in seen:unique.append(r);seen.add(r['actor_key'])
        return private_response(JsonResponse({'events':items,'actions':audit.ACTIONS,'object_types':audit.TYPES,'results':audit.RESULTS,
            'actors':unique[:200],'current_actor':audit.actor(),'can_send':False,'timezone':timezone.get_current_timezone_name(),
            'pagination':{'page':current.number,'pages':pages.num_pages,'total':pages.count}}))
    except (ValueError,OverflowError):return error('Проверьте фильтры, период и номер страницы журнала.')


@require_POST
def operator(request):
    ctx=audit.context.get()
    if not ctx:return error('Нет контекста оператора.',409)
    if audit.actor()['source'] in ('authenticated','remote_user'):return error('Имя берётся из авторизованной учётной записи; метка его не заменяет.',409)
    data=body(request);name=data.get('name')
    if not isinstance(name,str) or not name.strip() or len(name)>120 or any(ord(c)<32 for c in name):return error('Метка оператора: от 1 до 120 символов без управляющих знаков.')
    before={'label':ctx['identity']['name']}
    previous=ctx['identity']
    ctx['identity']={**previous,'name':name.strip()}
    try:
        audit.record('settings_changed','operator',0,'Локальная метка',before=before,after={'label':name.strip()},
            summary='Самоуказанная метка оператора изменена; личность не подтверждена входом.')
    except Exception:
        ctx['identity']=previous
        raise
    return private_response(JsonResponse({'actor':audit.actor(),'verified':False}))

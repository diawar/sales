from django.http import JsonResponse, HttpResponse, HttpResponseRedirect
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from . import servercore
from .models import EmailTemplate, EmailCampaign, TrackedMessage, TrackingEvent
from .mail_tracking import prepare_preview, resolve_link, observe
from .views import body, error

PIXEL=bytes.fromhex('47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b')


def private_response(response):
    response['Cache-Control']='no-store, no-cache, max-age=0'
    response['Referrer-Policy']='no-referrer'
    response['X-Robots-Tag']='noindex, nofollow'
    return response


@require_http_methods(['GET','POST'])
def connection(request):
    if request.method=='GET': return private_response(JsonResponse(servercore.configuration()))
    if body(request).get('action')!='inspect': return error('Доступна только проверка API, без отправки.')
    try: return private_response(JsonResponse(servercore.inspect_connection()))
    except ValueError as exc: return error(str(exc),400)


@require_POST
def preview(request):
    data=body(request)
    if data.get('is_test',True) is not True or data.get('send',False) is not False:
        return error('На этом этапе доступна только проверка без отправки.')
    if type(data.get('template_id')) is not int: return error('Выберите сохранённый шаблон.')
    template=EmailTemplate.objects.filter(pk=data['template_id']).first()
    if not template: return error('Шаблон не найден.',404)
    try: return private_response(JsonResponse(prepare_preview(request,template,data.get('recipient',''),data.get('values',{})),status=201))
    except ValueError as exc: return error(str(exc))


@require_http_methods(['GET','HEAD'])
def pixel(request,token):
    link=resolve_link(token,'open')
    # Unknown tokens return the same image and reveal no recipient information.
    if link and request.method=='GET': observe(link)
    return private_response(HttpResponse(PIXEL,content_type='image/gif'))


@require_http_methods(['GET','HEAD'])
def click(request,token):
    link=resolve_link(token,'click')
    if not link: return private_response(HttpResponse('Ссылка недоступна.',status=404,content_type='text/plain; charset=utf-8'))
    if request.method=='GET': observe(link)
    # Destination is captured from validated template; request parameters cannot replace it.
    return private_response(HttpResponseRedirect(link.target))


@require_GET
def report(request):
    params=request.GET
    mode=params.get('mode','test');kind=params.get('kind','');query=params.get('query','').strip()
    direction=params.get('direction','desc')
    if mode not in ('test','real') or kind not in ('','open','click','unsubscribe') or len(query)>254 or direction not in ('asc','desc'):
        return error('Проверьте фильтры событий.')
    messages=TrackedMessage.objects.filter(is_test=mode=='test')
    campaign_id=params.get('campaign_id','')
    campaign=None
    if campaign_id:
        if not campaign_id.isascii() or not campaign_id.isdigit() or len(campaign_id)>10:
            return error('Некорректный идентификатор рассылки.')
        campaign=EmailCampaign.objects.filter(pk=int(campaign_id)).first()
        if not campaign:return error('Рассылка не найдена.',404)
        messages=messages.filter(campaign=campaign)
    mid=params.get('message_id','')
    if mid:
        import uuid
        try: messages=messages.filter(pk=uuid.UUID(mid))
        except ValueError: return error('Некорректный идентификатор письма.')
    if query: messages=messages.filter(recipient__icontains=query)
    events=TrackingEvent.objects.filter(message__in=messages)
    from datetime import datetime, time, timedelta
    from django.utils import timezone
    from django.utils.dateparse import parse_date
    dates={}
    try:
        for key in ('from','to'):
            raw=params.get(key,'')
            value=parse_date(raw) if raw and len(raw)==10 else None
            if raw and (value is None or not 1970<=value.year<=9998):raise ValueError()
            dates[key]=value
        if dates['from'] and dates['to'] and dates['from']>dates['to']:raise ValueError()
    except (ValueError,TypeError):return error('Проверьте период событий (1970–9998).')
    if dates['from']:events=events.filter(occurred_at__gte=timezone.make_aware(datetime.combine(dates['from'],time.min)))
    if dates['to']:events=events.filter(occurred_at__lt=timezone.make_aware(datetime.combine(dates['to']+timedelta(days=1),time.min)))
    totals={};unique={}
    for name in ('open','click','unsubscribe'):
        values=events.filter(kind=name)
        totals[name]=values.count();unique[name]=values.values('message__recipient').distinct().count()
    history=events.select_related('message','link')
    if kind: history=history.filter(kind=kind)
    from django.core.paginator import Paginator
    try:
        page=int(params.get('page','1'))
        if not 1<=page<=2147483647: raise ValueError()
    except ValueError: return error('Некорректная страница.')
    history=history.order_by(('' if direction=='asc' else '-')+'occurred_at',('' if direction=='asc' else '-')+'pk')
    paginator=Paginator(history,25);current=paginator.page(min(page,paginator.num_pages))
    return private_response(JsonResponse({'mode':mode,'can_send':False,'messages_count':messages.count(),
        'campaign_id':campaign.pk if campaign else None,'campaign_name':campaign.name if campaign else '',
        'totals':totals,'unique':unique,
        'messages':[{'id':str(m.pk),'recipient':m.recipient,'subject':m.subject,'is_test':m.is_test,'state':m.state,
                     'created_at':m.created_at,'campaign_id':m.campaign_id,'preparation_snapshot':m.preparation_snapshot} for m in messages.order_by('-created_at')[:50]],
        'history':[{'recipient':e.message.recipient,'message_id':str(e.message_id),'subject':e.message.subject,'kind':e.kind,
                    'campaign_id':e.message.campaign_id,'preparation_snapshot':e.message.preparation_snapshot,
                    'target':e.link.target if e.link else '', 'occurred_at':e.occurred_at} for e in current],
        'pagination':{'page':current.number,'pages':paginator.num_pages,'total':paginator.count,
                      'has_next':current.has_next(),'has_previous':current.has_previous()}}))

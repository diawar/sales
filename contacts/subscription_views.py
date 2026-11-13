from django.conf import settings
from django.core.paginator import Paginator
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError
from urllib.parse import urlsplit
from django.http import JsonResponse,HttpResponse
from django.middleware.csrf import get_token
from django.urls import reverse
from django.utils.html import format_html
from django.utils import timezone
from django.views.decorators.http import require_GET,require_POST,require_http_methods
from .models import EmailSubscription,OverShopSubscription
from .subscriptions import request_subscription,unsubscribe_link,lookup,token_error,apply,CONSENT_TEXT,CONSENT_VERSION
from .overshop_sync import subscription_state
from .views import body,error

LABELS={'pending':'Ожидает подтверждения','confirmed':'Подтверждена','revoked':'Отозвана','inactive':'Неактивна','review':'На проверке','suppressed':'Исключена из отправки'}


def record(row,full=False):
    value={key:getattr(row,key) for key in ('id','email','scope','is_test','state','source','source_url','source_at','evidence','consent_text','consent_version','requested_at','confirmed_at','revoked_at')}
    value['label']=LABELS.get(row.state,row.state)
    if full:value['history']=[{'kind':e.kind,'source':e.source,'created_at':e.created_at,'snapshot':e.snapshot} for e in row.history.order_by('-pk')[:100]]
    return value


def link_data(request,row,raw,token):
    base=getattr(settings,'SALES_PUBLIC_BASE_URL','').rstrip('/')
    if base:
        try:
            URLValidator(schemes=['http','https'])(base)
            parsed=urlsplit(base)
            if parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValidationError('origin')
        except (ValidationError,ValueError):raise ValueError('Проверьте публичный адрес ссылок в настройках сервера.')
    path=reverse('subscription-public',args=[raw])
    # The deployment can set a public HTTPS origin; local development uses the current origin.
    url=base+path if base else request.build_absolute_uri(path)
    return {'subscription':record(row,True),'url':url,'action':token.action,'expires_at':token.expires_at,'sent':False,
            'note':'Письмо не отправлено. Ссылка показана сейчас; в БД хранится только её хеш. Подтверждение ссылки не является доказательством доставки в почтовый ящик.'}


@require_http_methods(['GET','POST'])
def subscriptions(request):
    if request.method=='POST':
        try:
            row,raw,token=request_subscription(body(request))
            return JsonResponse(link_data(request,row,raw,token),status=201)
        except ValueError as exc:return error(str(exc))
    params=request.GET;query=params.get('query','').strip();state=params.get('state','');origin=params.get('origin','local');mode=params.get('mode','all')
    if len(query)>254 or state not in ('',*LABELS) or origin not in ('local','overshop') or mode not in ('all','test','real'):return error('Проверьте фильтры подписок.')
    try:
        page=int(params.get('page',1))
        if not 1<=page<=2147483647:raise ValueError()
    except (TypeError,ValueError):return error('Некорректная страница.')
    if origin=='local':
        rows=EmailSubscription.objects.filter(scope='over_email_offers').order_by('-requested_at','-pk')
        if query:rows=rows.filter(email__icontains=query)
        if state:rows=rows.filter(state=state)
        if mode!='all':rows=rows.filter(is_test=mode=='test')
        paginator=Paginator(rows,25);current=paginator.page(min(page,paginator.num_pages));values=[record(r) for r in current]
    else:
        rows=OverShopSubscription.objects.select_related('address').order_by('-observed_at','-pk')
        if query:rows=rows.filter(address__email__icontains=query)
        rows=[r for r in rows if (not state or subscription_state(r)==state) and mode!='test']
        paginator=Paginator(rows,25);current=paginator.page(min(page,paginator.num_pages))
        values=[{'id':r.pk,'email':r.address.email,'is_test':False,'state':subscription_state(r),'label':LABELS[subscription_state(r)],
                 'source':'Импорт Over-shop.kz','source_at':r.observed_at,'confirmed_at':r.source_confirmed_at,'rubrics':r.rubrics,
                 'source_url':'https://over-shop.kz/bitrix/admin/subscr_edit.php?lang=ru&ID='+r.subscriber_id,
                 'note':'Сведения источника. История центра ведётся отдельно; импорт не снимает местные запреты.'} for r in current]
    return JsonResponse({'subscriptions':values,'origin':origin,'consent_text':CONSENT_TEXT,'consent_version':CONSENT_VERSION,
                         'pagination':{'page':current.number,'pages':paginator.num_pages,'total':paginator.count,'has_next':current.has_next(),'has_previous':current.has_previous()}})


@require_GET
def detail(request,subscription_id):
    row=EmailSubscription.objects.filter(pk=subscription_id).first()
    return JsonResponse(record(row,True)) if row else error('Подписка не найдена.',404)


@require_POST
def create_unsubscribe_link(request,subscription_id):
    try:
        row,raw,token=unsubscribe_link(subscription_id)
        return JsonResponse(link_data(request,row,raw,token))
    except EmailSubscription.DoesNotExist:return error('Подписка не найдена.',404)
    except ValueError as exc:return error(str(exc))


def public_page(request,title,message,row=None,action=None,status=200):
    info='';form=''
    if row:
        local,domain=row.email.rsplit('@',1);masked=local[:1]+'***@'+domain
        info=format_html('<p>{}</p><p class="mode">{}</p>',masked,'ТЕСТОВАЯ ЗАПИСЬ. Реальная клиентская база не меняется.' if row.is_test else 'Рабочая запись. Отписка остановит рекламные email OVER на этот адрес.')
    if action:
        csrf=get_token(request)
        consent=format_html('<label><input type="checkbox" name="consent" value="yes" required> {}</label><p class="version">Версия согласия: {}</p>',row.consent_text,row.consent_version) if action=='confirm' else ''
        form=format_html('<form method="post"><input type="hidden" name="csrfmiddlewaretoken" value="{}">{}<button type="submit">{}</button></form>',csrf,consent,'Подтвердить подписку' if action=='confirm' else 'Отписаться от акций OVER')
    html=format_html('<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{}</title><script src="/subscription-form.js" defer></script><style>body{{margin:0;padding:24px;background:#f3f5f7;color:#152b3c;font:16px/1.6 Arial,sans-serif}}main{{max-width:600px;margin:8vh auto;background:white;border-radius:12px;padding:28px;box-shadow:0 2px 12px #152b3c12}}h1{{font-size:26px}}button{{background:#0795e9;color:white;border:0;border-radius:6px;padding:12px 20px;cursor:pointer}}label{{display:block;margin:20px 0}}.mode{{background:#fff8e8;padding:12px;border-radius:6px}}.version{{font-size:12px;color:#65768a}}</style></head><body><main><h1>{}</h1><p id="subscription-message">{}</p>{}{}</main></body></html>',title,title,message,info,form)
    response=HttpResponse(html,status=status)
    response['Cache-Control']='no-store';response['Referrer-Policy']='no-referrer';response['X-Robots-Tag']='noindex, nofollow'
    response['Content-Security-Policy']="default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    return response


@require_http_methods(['GET','POST'])
def public(request,token):
    value=lookup(token);problem=token_error(value)
    if problem:return public_page(request,'Ссылка недоступна',problem,status=410 if value else 404)
    row=value.subscription
    if request.method=='POST':
        try:row,changed=apply(token,request.POST.get('consent')=='yes')
        except ValueError as exc:
            if 'application/json' in request.headers.get('Accept',''):return JsonResponse({'error':str(exc)},status=400)
            return public_page(request,'Действие не выполнено',str(exc),row,value.action,status=400)
        message=('Подписка подтверждена.' if row.state=='confirmed' else 'Вы отписаны от акций и предложений OVER.' if row.state=='revoked' else 'Эта ссылка уже использована; текущая подписка ожидает нового подтверждения.')
        message+=(' Тестовая запись: реальные контакты и запреты не менялись.' if row.is_test else '')
        if 'application/json' in request.headers.get('Accept',''):return JsonResponse({'state':row.state,'message':message,'is_test':row.is_test})
        return public_page(request,'Готово',message,row)
    if value.action=='confirm' and value.used_at:
        return public_page(request,'Ссылка уже использована','Текущий статус: '+LABELS[row.state]+'. Повторное открытие не меняет подписку.',row)
    if value.action=='unsubscribe' and row.state=='revoked':
        return public_page(request,'Вы уже отписаны','Подписка отозвана. Повторное открытие ничего не меняет.',row)
    return public_page(request,'Подтверждение подписки' if value.action=='confirm' else 'Отписка от рассылки',
        'Прочитайте условия и подтвердите согласие.' if value.action=='confirm' else 'Нажмите кнопку, чтобы отказаться от акций и предложений OVER. Простое открытие ссылки не меняет подписку.',row,value.action)

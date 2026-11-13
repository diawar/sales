from django.core.paginator import Paginator
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .notifications import visible,unread,KINDS
from .models import AppNotification,NotificationRead
from .views import body,error
from .accounts import private

@require_http_methods(['GET','POST'])
def center(request):
    if not request.user.is_authenticated:return private(error('Требуется вход.',401))
    if request.method=='POST':
        data=body(request);action=data.get('action');items=data.get('items')
        if action not in ('read','unread') or not isinstance(items,list) or not 1<=len(items)<=100:
            return private(error('Выберите от 1 до 100 уведомлений и действие.'))
        if any(not isinstance(item,dict) or type(item.get('id')) is not int or not 0<item['id']<2**63 or type(item.get('version')) is not int or not 0<item['version']<2**31 for item in items):
            return private(error('Проверьте идентификаторы и версии уведомлений.'))
        requested={item['id']:item['version'] for item in items}
        if len(requested)!=len(items):return private(error('Уведомления в списке не должны повторяться.'))
        with transaction.atomic():
            # Lock the notification revision as well as receipts; stale UI cannot read a newer error.
            list(AppNotification.objects.select_for_update().filter(pk__in=requested).order_by('pk'))
            rows=list(visible(request.user).filter(pk__in=requested))
            if len(rows)!=len(requested):return private(error('Уведомление не найдено или недоступно.',404))
            if any(row.version!=requested[row.pk] for row in rows):return private(error('Уведомление обновилось. Обновите список перед отметкой.',409))
            for row in rows:
                if action=='read':NotificationRead.objects.update_or_create(notification=row,user=request.user,defaults={'version':row.version,'read_at':timezone.now()})
                else:NotificationRead.objects.filter(notification=row,user=request.user).delete()
        return private(JsonResponse({'updated':len(rows),'unread':unread(request.user),'can_send':False}))
    kind=request.GET.get('kind','');state=request.GET.get('state','all')
    try:page=int(request.GET.get('page','1'))
    except (ValueError,OverflowError):return private(error('Проверьте номер страницы.'))
    if kind not in ('',*KINDS) or state not in ('all','unread') or not 1<=page<=1000000:return private(error('Проверьте фильтры уведомлений.'))
    rows=visible(request.user)
    if kind:rows=rows.filter(kind=kind)
    if state=='unread':rows=rows.filter(is_read=False,resolved=False)
    pages=Paginator(rows.order_by('-updated_at','-pk'),25);current=pages.page(min(page,pages.num_pages))
    items=[{'id':n.pk,'version':n.version,'kind':n.kind,'title':n.title,'message':n.message,'read':n.is_read,
        'resolved':n.resolved,'created_at':n.created_at,'updated_at':n.updated_at,
        'target':{'type':n.target,'object_type':n.object_type,'object_id':n.object_id,'campaign_id':n.campaign_id,'audit_id':n.audit_id}} for n in current.object_list]
    return private(JsonResponse({'items':items,'unread':unread(request.user),'kinds':KINDS,
        'pagination':{'page':current.number,'pages':pages.num_pages,'total':pages.count},'can_send':False}))

from django.core.paginator import Paginator
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from .models import CollectionLog

@require_GET
def journal(request):
    items=CollectionLog.objects.order_by('-id')
    source=request.GET.get('source','');level=request.GET.get('level','');query=request.GET.get('q','').strip()[:200]
    if source:items=items.filter(source=source)
    if level:items=items.filter(level=level)
    if query:items=items.filter(message__icontains=query)
    paginator=Paginator(items,30);page=paginator.get_page(request.GET.get('page',1))
    return JsonResponse({'events':list(page.object_list.values('id','at','source','level','message')),'page':page.number,'pages':paginator.num_pages,'count':paginator.count})

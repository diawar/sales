from datetime import timedelta
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Count
from django.views.decorators.http import require_http_methods
from .models import ImportBatch,KaspiCollection,KaspiTask,KaspiMerchant,Phone,Contact
from .kaspi_collection import control
from .kaspi_client import CATEGORIES,CATEGORY_MAP
from .views import body,error

@require_http_methods(['GET','POST'])
def status(request):
    if request.method=='POST':
        try:control(body(request).get('action'))
        except ValueError as exc:return error(str(exc))
    state,_=KaspiCollection.objects.get_or_create(pk=1)
    rows=[]
    for merchant in KaspiMerchant.objects.filter(client__isnull=False).order_by('name'):
        rows.append({'merchant_id':merchant.merchant_id,'shop_name':merchant.name,'city':merchant.city,
                     'categories':[CATEGORY_MAP[c] for c in merchant.category_codes],
                     'phones':merchant.phones,'addresses':merchant.addresses,
                     'source_url':'https://kaspi.kz/shop/m/'+merchant.merchant_id+'/products/'})
    if not rows:
        batch=ImportBatch.objects.filter(result__kind='kaspi_pilot').order_by('-committed_at').first()
        rows=batch.rows if batch else []
    total=len(rows);category=request.GET.get('category','');query=request.GET.get('q','').strip().casefold()[:100]
    rows=[r for r in rows if (not category or category in r['categories']) and
          (not query or any(query in str(r.get(k,'')).casefold() for k in ['shop_name','city','merchant_id']))]
    rows.sort(key=lambda r:r['shop_name'].casefold())
    try:page=max(1,int(request.GET.get('page','1')))
    except ValueError:return error('Неверная страница.')
    count=len(rows);pages=max(1,(count+19)//20);page=min(page,pages)
    counts={r['state']:r['n'] for r in KaspiTask.objects.filter(cycle=state.cycle).values('state').annotate(n=Count('id'))}
    return JsonResponse({'available':bool(total),'automatic':True,'enabled':state.enabled,'city':'Алматы',
        'worker_alive':bool(state.heartbeat_at and state.heartbeat_at>timezone.now()-timedelta(seconds=90)),
        'busy':bool(state.lease_until and state.lease_until>timezone.now()),'error':state.error,'counts':counts,
        'next_request_at':state.next_request_at,'completed_at':state.completed_at,'cycle':state.cycle,'report':state.report,
        'result':{'merchants':total,'phones':Phone.objects.filter(source='Kaspi').values('number').distinct().count(),
                  'emails':Contact.objects.filter(evidence__source='Kaspi',address__isnull=False).values('address_id').distinct().count()},
        'rows':rows[(page-1)*20:page*20],'total':count,'page':page,'pages':pages,'categories':[c['name'] for c in CATEGORIES]})

import json
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from .overshop_source import CATEGORY_LABELS, PURCHASE_LABELS

@require_GET
def preview(request):
    full_path=settings.BASE_DIR/'data/overshop/full-review.json'
    if full_path.exists():
        try:
            report=json.loads(full_path.read_text(encoding='utf-8'))
            rows=report['records']
            category=request.GET.get('category','person_candidate')
            purchase=request.GET.get('purchase','')
            sort=request.GET.get('sort','id_desc')
            sorts={'id_desc':('source_customer_id',True),'id_asc':('source_customer_id',False),
                   'orders_desc':('orders_total',True),'orders_asc':('orders_total',False),
                   'name':('name',False),'email':('email',False)}
            if (category and category not in CATEGORY_LABELS) or (purchase and purchase not in PURCHASE_LABELS) or sort not in sorts:
                return JsonResponse({'error':'Неизвестный фильтр или сортировка.'},status=400)
            try:page=max(1,int(request.GET.get('page','1')))
            except ValueError:return JsonResponse({'error':'Неверный номер страницы.'},status=400)
            query=request.GET.get('q','').strip().casefold()[:100]
            rows=[r for r in rows if (not category or r['category']==category) and
                  (not purchase or r['purchase_segment']==purchase) and
                  (not query or any(query in str(r.get(k,'')).casefold() for k in ['source_customer_id','name','email','phone','company']))]
            field,reverse=sorts[sort]
            rows.sort(key=lambda r:int(r[field]) if field in {'source_customer_id','orders_total'} else r[field].casefold(),reverse=reverse)
            total=len(rows);pages=max(1,(total+19)//20);page=min(page,pages)
            pilot_path=settings.BASE_DIR/'data/overshop/pilot-result.json'
            pilot=json.loads(pilot_path.read_text(encoding='utf-8')) if pilot_path.exists() else {}
            return JsonResponse({'available':True,'full_review':True,'connected':False,'imported':bool(pilot),'full_imported':False,
                'pilot':{key:pilot[key] for key in ['created','updated','verified','audience_id','source_ids'] if key in pilot},
                'summary':report['summary'],'categories':CATEGORY_LABELS,'purchases':PURCHASE_LABELS,
                'total':total,'page':page,'pages':pages,'preview':rows[(page-1)*20:page*20]})
        except (ValueError,KeyError,OSError,TypeError):
            return JsonResponse({'error':'Не удалось прочитать проверку Over-shop.kz.'},status=503)
    path=settings.BASE_DIR/'data/overshop/buyers-preview.json'
    if not path.exists():return JsonResponse({'available':False,'connected':False})
    try:
        report=json.loads(path.read_text(encoding='utf-8'))
        return JsonResponse({'available':True,'connected':False,'imported':False,
            **{key:report[key] for key in ['records','unique_valid_emails','stats','blockers','preview']}})
    except (ValueError,KeyError,OSError):
        return JsonResponse({'error':'Не удалось прочитать предпросмотр Over-shop.kz.'},status=503)

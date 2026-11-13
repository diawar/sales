from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from .views import body,error
from .campaign_statistics import statistics,add_event,demo,KINDS

@require_GET
def report(request):
    try: return JsonResponse(statistics(request.GET)|{'kinds':KINDS})
    except ValueError as exc: return error(str(exc))

@require_POST
def test_event(request):
    try:
        row,created=add_event(body(request))
        return JsonResponse({'event_id':str(row.event_id),'created':created,'source':'test'},status=201 if created else 200)
    except ValueError as exc: return error(str(exc))

@require_POST
def generate_demo(request):
    try: return JsonResponse({'added':demo(body(request).get('campaign_id')),'source':'test'})
    except ValueError as exc: return error(str(exc))

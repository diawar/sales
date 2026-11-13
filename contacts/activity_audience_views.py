from django.http import JsonResponse
from django.views.decorators.http import require_GET,require_POST
from .activity_audiences import report,populate_demo,add_test_event
from .views import error,body

@require_GET
def audiences(request):
    try:return JsonResponse(report(request.GET))
    except ValueError as exc:return error(str(exc))

@require_POST
def demo(request):
    return JsonResponse({'added':populate_demo(),'test_only':True,'can_send':False})


@require_POST
def test_event(request):
    try:
        row,created=add_test_event(body(request))
        return JsonResponse({'event_id':str(row.event_id),'created':created,'test_only':True},status=201 if created else 200)
    except ValueError as exc:return error(str(exc))

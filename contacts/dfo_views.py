from datetime import timedelta
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import DfoCollection
from .dfo_collection import control
from .views import body,error

@require_http_methods(['GET','POST'])
def status(request):
    if request.method=='POST':
        try:control(body(request).get('action'),body(request).get('mode'))
        except ValueError as exc:return error(str(exc))
    state,_=DfoCollection.objects.get_or_create(pk=1)
    return JsonResponse({'enabled':state.enabled,'mode':state.mode,'next_page':state.next_page,'page_limit':2 if state.mode=='pilot' else None,'report':state.report,
        'error':state.error,'completed_at':state.completed_at,'next_attempt_at':state.next_attempt_at,
        'worker_alive':bool(state.heartbeat_at and state.heartbeat_at>timezone.now()-timedelta(seconds=90)),
        'busy':bool(state.lease_until and state.lease_until>timezone.now())})

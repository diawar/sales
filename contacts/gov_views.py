from datetime import timedelta
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import GovCollection
from .gov_collection import control
from .views import body,error

@require_http_methods(['GET','POST'])
def status(request):
    if request.method=='POST':
        payload=body(request)
        try:control(payload.get('action'),payload.get('urls'))
        except ValueError as exc:return error(str(exc))
    state,_=GovCollection.objects.get_or_create(pk=1)
    return JsonResponse({'enabled':state.enabled,'urls':state.urls,'cursor':state.cursor,'total':len(state.urls),'report':state.report,'error':state.error,'completed_at':state.completed_at,'next_attempt_at':state.next_attempt_at,'worker_alive':bool(state.heartbeat_at and state.heartbeat_at>timezone.now()-timedelta(seconds=90)),'busy':bool(state.lease_until and state.lease_until>timezone.now())})

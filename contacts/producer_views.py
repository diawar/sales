from datetime import timedelta
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import ProducerPilot
from .producers import control
from .views import body,error

@require_http_methods(['GET','POST'])
def status(request):
    if request.method=='POST':
        try:control(body(request).get('action'))
        except ValueError as exc:return error(str(exc))
    state,_=ProducerPilot.objects.get_or_create(pk=1)
    return JsonResponse({'enabled':state.enabled,'status':state.status,'next_page':state.next_page,'worker_alive':bool(state.heartbeat_at and state.heartbeat_at>timezone.now()-timedelta(seconds=90)),'busy':bool(state.lease_until and state.lease_until>timezone.now()),'error':state.error,'report':state.report if state.report.get('mode')=='automatic' else {},'started_at':state.started_at,'completed_at':state.completed_at,'next_attempt_at':state.next_attempt_at})

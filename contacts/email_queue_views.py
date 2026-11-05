from datetime import timedelta
from django.http import JsonResponse
from django.db import transaction
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import Address,EmailCheckState
from .email_queue import candidates
from .views import body,error


@require_http_methods(['GET','POST'])
def status(request):
    state,_=EmailCheckState.objects.get_or_create(pk=1)
    if request.method=='POST':
        action=body(request).get('action')
        if action not in {'start','pause'}:return error('Выберите запуск или паузу.')
        with transaction.atomic():
            state=EmailCheckState.objects.select_for_update().get(pk=1)
            state.enabled=action=='start';state.error='';state.save(update_fields=['enabled','error'])
    eligible=Address.objects.filter(archived=False,suppression_reason='')
    return JsonResponse({'enabled':state.enabled,'error':state.error,
        'worker_alive':bool(state.heartbeat_at and state.heartbeat_at>timezone.now()-timedelta(seconds=90)),
        'total':eligible.count(),'checked':eligible.filter(validation_checked_at__isnull=False).count(),
        'due':candidates().count(),'retry':eligible.filter(validation_next_at__isnull=False).count()})

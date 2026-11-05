from datetime import timedelta
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from .models import CollectionState,CollectionItem,CollectionParticipant,CollectionLog,Contact,Phone,Evidence
from .collection import configure,log
from .views import body,error

@require_http_methods(['GET','POST'])
def status(request):
    if request.method=='POST':
        value=body(request);action=value.get('action')
        if action not in {'start','pause','retry'}:return error('Неизвестное действие.')
        days=value.get('days')
        try:
            if action=='retry':
                for Model in (CollectionItem,CollectionParticipant):
                    Model.objects.filter(state='review').update(state='retry',attempts=0,next_attempt_at=timezone.now(),error='')
                log('Записи для проверки возвращены в очередь.')
            else:configure(action,days)
        except ValueError as exc:return error(str(exc))
    state,_=CollectionState.objects.get_or_create(pk=1)
    now=timezone.now()
    return JsonResponse({'enabled':state.enabled,'lookback_days':state.lookback_days,'worker_alive':bool(state.heartbeat_at and state.heartbeat_at>now-timedelta(seconds=90)), 'busy':bool(state.lease_until and state.lease_until>now),'heartbeat_at':state.heartbeat_at,'completed_at':state.completed_at,'next_scan_at':state.next_scan_at,'windows_left':len(state.windows),'error':state.error,'counts':{'processed':CollectionItem.objects.filter(state='done').count(),'queued':CollectionItem.objects.filter(state__in=['queued','retry','processing']).count(),'review':CollectionItem.objects.filter(state='review').count()+CollectionParticipant.objects.filter(state='review').count(),'companies':CollectionParticipant.objects.count(),'contacts':Contact.objects.filter(evidence__source='Goszakup').distinct().count(),'emails':Contact.objects.filter(evidence__source='Goszakup',address__isnull=False).values('address_id').distinct().count(),'phones':Phone.objects.filter(source='Goszakup').values('number').distinct().count()},'logs':list(CollectionLog.objects.order_by('-id').values('at','level','message')[:30])})

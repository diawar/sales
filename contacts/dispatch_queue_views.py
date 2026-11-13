from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from .models import EmailCampaign, CampaignQueue
from .views import body, error
from .dispatch_queue import prepare, control, report, worker_alive, SCENARIOS


@require_http_methods(['GET','POST'])
def queue(request,campaign_id):
    if request.method=='GET':
        if not EmailCampaign.objects.filter(pk=campaign_id).exists(): return error('Рассылка не найдена.',404)
        row=CampaignQueue.objects.filter(campaign_id=campaign_id).first()
    else:
        data=body(request)
        try:
            with transaction.atomic():
                campaign=EmailCampaign.objects.select_for_update().filter(pk=campaign_id).first()
                if not campaign: return error('Рассылка не найдена.',404)
                action=data.get('action')
                if action=='prepare': row=prepare(campaign,data)
                else:
                    row=CampaignQueue.objects.select_for_update().filter(campaign=campaign).first()
                    if not row: return error('Сначала подготовьте очередь.',409)
                    control(row,action,data)
        except ValueError as exc: return error(str(exc),409)
    return JsonResponse({'queue':report(row) if row else None,'worker_alive':worker_alive(),
                         'scenarios':SCENARIOS,'mode':'simulation'})

from django.http import JsonResponse
from django.views.decorators.http import require_GET
from .models import EmailCampaign
from .views import error
from .campaign_readiness import report

@require_GET
def readiness(request,campaign_id):
    row=EmailCampaign.objects.select_related('template','dispatch_queue').filter(pk=campaign_id).first()
    if not row:return error('Рассылка не найдена.',404)
    try:return JsonResponse(report(row))
    except ValueError as exc:return error(str(exc))

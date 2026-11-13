from django.http import JsonResponse
from django.views.decorators.http import require_http_methods, require_GET
from . import rehearsal
from .models import CampaignRehearsal, AutomationScenario
from .views import body,error
from .mail_integration_views import private_response


@require_http_methods(['GET','POST'])
def sessions(request,rehearsal_id=None):
    try:
        if request.method=='POST':
            data=body(request)
            row=rehearsal.start(request,data) if rehearsal_id is None else rehearsal.act(request,rehearsal_id,data)
            return private_response(JsonResponse(rehearsal.report(row,{}),status=201 if rehearsal_id is None else 200))
        if rehearsal_id is None:
            return private_response(JsonResponse({'can_send':False,'sessions':list(CampaignRehearsal.objects.order_by('-pk')[:200].values('id','snapshot__name','state','clock_at'))}))
        return private_response(JsonResponse(rehearsal.report(CampaignRehearsal.objects.get(pk=rehearsal_id),request.GET)))
    except (CampaignRehearsal.DoesNotExist,AutomationScenario.DoesNotExist):return error('Сценарий или репетиция не найдены.',404)
    except (ValueError,TypeError,OverflowError) as exc:return error(str(exc))


@require_GET
def letter(request,rehearsal_id,letter_id):
    row=CampaignRehearsal.objects.filter(pk=rehearsal_id).first()
    letter=row.letters.select_related('message').filter(pk=letter_id).first() if row else None
    if not letter:return error('Письмо не найдено в этой репетиции.',404)
    return private_response(JsonResponse({'id':letter.pk,'html':letter.preview_html,'checks':letter.checks,
        'delivery_events':list(letter.delivery_events.order_by('pk').values('event_id','attempt','kind','detail','occurred_at')),
        'snapshot':letter.message.preparation_snapshot,'subject':letter.message.subject,'can_send':False,'sent':False}))

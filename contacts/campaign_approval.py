"""Approval of a specific campaign version. No transport or recipient mutations."""
import hashlib
import json
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import CampaignApproval,EmailCampaign
from .access import role,is_admin
from .views import body,error
from . import action_audit as audit

LABELS={'draft':'Не согласована','pending':'На проверке','approved':'Одобрена','returned':'Возвращена','invalidated':'Нужно повторное согласование'}

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,cls=DjangoJSONEncoder,separators=(',',':')).encode()).hexdigest()

def signature(campaign):
    # Load related records anew: a cached FK must not conceal a concurrent library edit.
    from .models import EmailTemplate,CampaignSender
    t=EmailTemplate.objects.get(pk=campaign.template_id)
    s=CampaignSender.objects.get(pk=campaign.sender_id)
    return digest({'revision':campaign.revision,'name':campaign.name,
        'audiences':sorted(campaign.audiences.values_list('pk',flat=True)),
        'template':campaign.template_snapshot,'sender':campaign.sender_snapshot,
        'library_template':{k:getattr(t,k) for k in ('revision','subject','preheader','html')},
        'library_sender':{k:getattr(s,k) for k in ('name','email','reply_to')},
        'mode':campaign.schedule_mode,'at':campaign.scheduled_at,'timezone':campaign.schedule_timezone})

def recipient_signature(campaign):
    from .dispatch_queue import manifest
    return digest([email for email,pk in manifest(campaign)])

def record(campaign,check_recipients=False):
    row=CampaignApproval.objects.filter(campaign=campaign).first()
    if not row:return {'status':'draft','label':LABELS['draft'],'version':0,'can_send':False}
    stale=row.status in ('pending','approved') and row.digest!=signature(campaign)
    if check_recipients and row.status in ('pending','approved'):
        try:stale=stale or row.recipient_digest!=recipient_signature(campaign)
        except ValueError:stale=True
    status='invalidated' if stale else row.status
    return {'status':status,'label':LABELS[status],'version':row.version,'campaign_revision':row.campaign_revision,
        'submitted_by':row.submitted_by,'submitted_at':row.submitted_at,'reviewed_by':row.reviewed_by,
        'reviewed_at':row.reviewed_at,'comment':row.comment,'snapshot':row.snapshot,'can_send':False,
        'reason':'Письмо, настройки или состав получателей изменились. Отправьте актуальную версию на проверку.' if stale else ''}

def invalidate(campaign,reason):
    row=CampaignApproval.objects.select_for_update().filter(campaign=campaign,status__in=('pending','approved')).first()
    if not row:return
    before={'status':row.status,'version':row.version};row.status='invalidated';row.version+=1;row.comment=reason;row.save()
    audit.record('approval_invalidated','campaign',campaign.pk,campaign.name,campaigns=[campaign.pk],before=before,
        after={'status':row.status,'version':row.version},summary=reason)

def require_approved(campaign,version=None,recipients=False):
    # Legacy feature tests explicitly disable authentication; deployed settings do not.
    if not settings.SALES_AUTH_REQUIRED:return 0
    row=CampaignApproval.objects.filter(campaign=campaign).first()
    if not row or row.status!='approved' or row.digest!=signature(campaign):
        raise ValueError('Рассылка «'+campaign.name+'» не имеет одобрения актуальной версии. Откройте согласование.')
    if version is not None and row.version!=version:raise ValueError('Одобрение запуска устарело. Нужен новый запуск согласованной версии.')
    if recipients and row.recipient_digest!=recipient_signature(campaign):
        raise ValueError('Состав получателей изменился после согласования. Отправьте рассылку на повторную проверку.')
    return row.version

def require_steps(snapshot):
    if not settings.SALES_AUTH_REQUIRED:return
    for step in snapshot['steps']:
        campaign=EmailCampaign.objects.get(pk=step['campaign_id'])
        require_approved(campaign,version=step.get('approval_version',-1))

@require_http_methods(['GET','POST'])
def approval(request,campaign_id):
    if request.method=='GET':
        campaign=EmailCampaign.objects.filter(pk=campaign_id).first()
        if not campaign:return error('Рассылка не найдена.',404)
        return JsonResponse({'approval':record(campaign,check_recipients=True),'can_submit':role(request.user) in ('admin','manager'),
            'can_review':is_admin(request.user),'can_send':False})
    data=body(request);action=data.get('action');comment=data.get('comment','')
    if not isinstance(comment,str) or len(comment)>2000:return error('Комментарий — до 2000 символов.')
    if action not in ('submit','approve','return'):return error('Неизвестное действие согласования.')
    if role(request.user) not in ('admin','manager') or action!='submit' and not is_admin(request.user):
        return error('Недостаточно прав для согласования.',403)
    with transaction.atomic():
        campaign=EmailCampaign.objects.select_for_update().filter(pk=campaign_id).first()
        if not campaign:return error('Рассылка не найдена.',404)
        if type(data.get('campaign_revision')) is not int or data['campaign_revision']!=campaign.revision:
            return error('Версия рассылки изменилась. Откройте согласование заново.',409)
        row,_=CampaignApproval.objects.get_or_create(campaign=campaign)
        if type(data.get('version')) is not int or data['version']!=row.version:
            return error('Решение изменилось в другом окне. Откройте согласование заново.',409)
        before={'status':row.status,'version':row.version};now=timezone.now()
        if action=='submit':
            from .campaign_views import recipients
            try:
                counts=recipients({'audience_ids':list(campaign.audiences.values_list('pk',flat=True))})
                recipients_digest=recipient_signature(campaign)
            except ValueError as exc:return error(str(exc))
            if row.status=='pending' and row.digest==signature(campaign) and row.recipient_digest==recipients_digest:return error('Рассылка уже на проверке.',409)
            row.status='pending';row.digest=signature(campaign);row.recipient_digest=recipients_digest
            row.campaign_revision=campaign.revision
            row.snapshot={'name':campaign.name,'audience_ids':sorted(campaign.audiences.values_list('pk',flat=True)),
                'audiences':list(campaign.audiences.order_by('pk').values_list('name',flat=True)),
                'template':campaign.template_snapshot,'sender':campaign.sender_snapshot,'counts':counts,
                'schedule_mode':campaign.schedule_mode,'scheduled_at':campaign.scheduled_at.isoformat() if campaign.scheduled_at else None,
                'timezone':campaign.schedule_timezone}
            row.submitted_by=request.user.username;row.submitted_at=now;row.reviewed_by='';row.reviewed_at=None
        else:
            if row.status!='pending':return error('Решение можно принять только по рассылке на проверке.',409)
            if row.digest!=signature(campaign) or row.recipient_digest!=recipient_signature(campaign):
                invalidate(campaign,'Письмо, настройки или состав получателей изменились во время проверки.')
                return error('Данные изменились. Нужно повторно отправить рассылку на проверку.',409)
            if action=='return' and not comment.strip():return error('Укажите причину возврата.')
            row.status='approved' if action=='approve' else 'returned'
            row.reviewed_by=request.user.username;row.reviewed_at=now
        row.comment=comment.strip();row.version+=1;row.save()
        kind={'submit':'approval_submitted','approve':'approval_approved','return':'approval_returned'}[action]
        audit.record(kind,'campaign',campaign.pk,campaign.name,campaigns=[campaign.pk],before=before,
            after={'status':row.status,'version':row.version,'campaign_revision':campaign.revision,'comment':row.comment},
            summary={'submit':'Рассылка отправлена на проверку.','approve':'Версия рассылки одобрена для имитации.','return':'Рассылка возвращена с комментарием.'}[action])
        return JsonResponse({'approval':record(campaign),'can_send':False})

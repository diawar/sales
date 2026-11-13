"""Internal notifications only. Message text is allowlisted; no raw errors or recipients."""
import hashlib
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q,Exists,OuterRef
from django.utils import timezone
from .models import AppNotification,NotificationRead,CampaignApproval,CampaignQueue
from .access import role

KINDS={'error':'Ошибки','stop':'Остановки','approval':'Согласование'}

@transaction.atomic
def emit(key,*,fingerprint='',reopen=False,**values):
    row,created=AppNotification.objects.get_or_create(key=key,defaults={**values,'fingerprint':fingerprint})
    if not created:
        row=AppNotification.objects.select_for_update().get(pk=row.pk)
        if row.fingerprint!=fingerprint or reopen and row.resolved:
            for field,value in values.items():setattr(row,field,value)
            row.fingerprint=fingerprint;row.version+=1;row.updated_at=timezone.now();row.save()
    return row

def resolve(**filters):
    AppNotification.objects.filter(resolved=False,**filters).update(resolved=True)

def visible(user):
    r=role(user)
    if not r:return AppNotification.objects.none()
    if r=='admin':rows=AppNotification.objects.all()
    else:
        scope=Q(visibility='all')|Q(owner=user)
        if r=='manager':scope|=Q(visibility='operations')
        rows=AppNotification.objects.filter(scope)
    reads=NotificationRead.objects.filter(user=user,notification_id=OuterRef('pk'),version=OuterRef('version'))
    return rows.annotate(is_read=Exists(reads))

def unread(user):return visible(user).filter(is_read=False,resolved=False).count()

def owner_id(entry):
    if entry.actor_source=='authenticated' and entry.actor_key.startswith('user:'):
        value=entry.actor_key[5:]
        if value.isdigit() and get_user_model().objects.filter(pk=int(value)).exists():return int(value)
    return None

def from_audit(entry):
    refs=list(entry.campaign_refs.values_list('campaign_id',flat=True));cid=refs[0] if refs else entry.object_id if entry.object_type=='campaign' else None
    target='queue' if entry.object_type=='queue' and cid else entry.object_type if entry.object_type in ('rehearsal','simulation') else 'journal'
    common={'object_type':entry.object_type,'object_id':entry.object_id,'campaign_id':cid,'audit_id':entry.pk}
    if entry.action.startswith('approval_') and entry.result=='success':
        resolve(campaign_id=cid,code='approval_wait',audit_id__lt=entry.pk)
        resolve(campaign_id=cid,code='approval_decision',audit_id__lt=entry.pk)
        if entry.action=='approval_submitted':
            approval=CampaignApproval.objects.filter(campaign_id=cid,status='pending',version=entry.after.get('version')).first()
            if approval:
                emit(f'approval:{cid}:{approval.version}',kind='approval',code='approval_wait',
                    title='Рассылка ожидает согласования',message='Версия передана на проверку. Администратор может одобрить её или вернуть с комментарием.',
                    visibility='admin',owner_id=owner_id(entry),target='approval',created_at=entry.occurred_at,updated_at=entry.occurred_at,**common)
        else:
            titles={'approval_approved':'Рассылка одобрена','approval_returned':'Рассылка возвращена','approval_invalidated':'Нужно повторное согласование'}
            emit(f'audit:{entry.pk}',kind='approval',code='approval_decision',title=titles[entry.action],
                message='Откройте согласование, чтобы посмотреть решение и комментарий. Реальная отправка отключена.',
                visibility='operations',target='approval',created_at=entry.occurred_at,updated_at=entry.occurred_at,**common)
        return
    if entry.action in ('resumed','completed','cancelled') and entry.result=='success':
        resolve(object_type=entry.object_type,object_id=entry.object_id,kind='stop',audit_id__lt=entry.pk)
        if entry.object_type=='rehearsal' and entry.action in ('completed','cancelled'):
            resolve(object_type='rehearsal',object_id=entry.object_id,kind='error')
    if entry.result=='failed':
        emit(f'audit:{entry.pk}',kind='error',code='action_error',title='Ошибка выполнения действия',
            message='Действие завершилось ошибкой сервера. Проверьте состояние объекта и журнал перед повтором.',
            visibility='admin' if entry.object_type=='account' else 'operations',owner_id=owner_id(entry),target=target,
            created_at=entry.occurred_at,updated_at=entry.occurred_at,**common)
    elif entry.action in ('auto_stopped','paused') and entry.result=='success':
        emit(f'audit:{entry.pk}',kind='stop',code='automatic_stop' if entry.action=='auto_stopped' else 'paused',
            title='Обработка остановлена защитой' if entry.action=='auto_stopped' else 'Обработка приостановлена',
            message='Имитация приостановлена. Причина и параметры сохранены в журнале; проверьте их перед продолжением.',
            visibility='all',target=target,created_at=entry.occurred_at,updated_at=entry.occurred_at,**common)
    elif entry.action=='test_event' and entry.object_type=='rehearsal' and entry.after.get('kind') in ('temporary_error','permanent_error','complaint'):
        kind=entry.after['kind'];labels={'temporary_error':'Временная ошибка доставки','permanent_error':'Постоянная ошибка доставки','complaint':'Тестовая жалоба получателя'}
        emit(f'rehearsal-error:{entry.object_id}',fingerprint=str(entry.pk),kind='error',code='delivery_error',title=labels[kind],
            message='В репетиции зарегистрирован тестовый результат доставки. Откройте журнал репетиции. SMTP не использовался.',
            visibility='all',target='rehearsal',**common)

def rehearsal_issue(sender,instance,created,raw=False,**kwargs):
    if raw or not created or instance.kind not in ('blocked','retry_exhausted'):return
    emit(f'rehearsal-check:{instance.rehearsal_id}',fingerprint=str(instance.pk),kind='error',code='preparation_error',
        title='Проверка письма или повтора не пройдена',
        message='В репетиции есть заблокированная подготовка письма или исчерпаны попытки. Откройте запуск и проверьте причины.',
        visibility='all',target='rehearsal',object_type='rehearsal',object_id=instance.rehearsal_id,resolved=False)

def queue_attempt(sender,instance,created,raw=False,**kwargs):
    if raw or not created or instance.outcome not in ('temporary','permanent'):return
    q=instance.item.queue
    emit(f'queue-error:{q.pk}',fingerprint=str(instance.pk),kind='error',code='queue_error',
        title='Ошибка в очереди имитации',message='В очереди зарегистрирована временная или постоянная тестовая ошибка. Проверьте повторы и результаты обработки.',
        visibility='all',object_type='queue',object_id=q.pk,campaign_id=q.campaign_id,target='queue',resolved=False)

def queue_state(sender,instance,raw=False,**kwargs):
    if raw:return
    if instance.state in ('completed','cancelled') and not instance.items.filter(state__in=('failed','retry')).exists():
        resolve(key=f'queue-error:{instance.pk}')

SOURCES={'CollectionState':'Goszakup','ProducerPilot':'e-ondiris','GovCollection':'Gov.kz','DfoCollection':'ДФО','KaspiCollection':'Kaspi'}

def source_state(sender,instance,raw=False,**kwargs):
    if raw:return
    key=f'source:{sender.__name__}:{instance.pk}'
    if not instance.error:resolve(key=key);return
    emit(key,fingerprint=hashlib.sha256(instance.error.encode()).hexdigest(),reopen=True,kind='error',code='source_error',
        title='Ошибка источника '+SOURCES[sender.__name__],message='Источник сообщил об ошибке. Откройте сбор данных и журнал источника для проверки.',
        visibility='admin',target='source',object_type='source',object_id=instance.pk,resolved=False)

"""Idempotent import of existing application state; never reruns jobs or sends mail."""
from django.core.management.base import BaseCommand
from contacts import models,notifications

class Command(BaseCommand):
    help='Подготовить уведомления из существующего журнала и состояния источников без отправки.'
    def handle(self,*args,**options):
        before=models.AppNotification.objects.count()
        actions=['approval_submitted','approval_approved','approval_returned','approval_invalidated','paused','auto_stopped','resumed','completed','cancelled','test_event']
        from django.db.models import Q
        for entry in models.ActionAuditEntry.objects.filter(Q(action__in=actions)|Q(result='failed')).order_by('pk').iterator():
            notifications.from_audit(entry)
        for name in notifications.SOURCES:
            model=getattr(models,name)
            for row in model.objects.all():notifications.source_state(model,row)
        for queue in models.CampaignQueue.objects.all():
            last=models.CampaignQueueAttempt.objects.filter(item__queue=queue,outcome__in=('temporary','permanent')).order_by('-pk').first()
            if last:notifications.queue_attempt(models.CampaignQueueAttempt,last,True)
            notifications.queue_state(models.CampaignQueue,queue)
        self.stdout.write(f'Добавлено уведомлений: {models.AppNotification.objects.count()-before}. Отправка не выполнялась.')

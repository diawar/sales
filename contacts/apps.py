from django.apps import AppConfig

class ContactsConfig(AppConfig):
    name='contacts'
    def ready(self):
        from django.db.models.signals import post_save
        from . import models,notifications
        post_save.connect(notifications.queue_attempt,sender=models.CampaignQueueAttempt,dispatch_uid='notifications-queue-attempt')
        post_save.connect(notifications.queue_state,sender=models.CampaignQueue,dispatch_uid='notifications-queue-state')
        post_save.connect(notifications.rehearsal_issue,sender=models.RehearsalLog,dispatch_uid='notifications-rehearsal-check')
        for model in notifications.SOURCES:
            post_save.connect(notifications.source_state,sender=getattr(models,model),dispatch_uid='notifications-'+model)

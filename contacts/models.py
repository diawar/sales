import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone

class AccountRole(models.Model):
    user=models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='sales_role')
    role=models.CharField(max_length=16,default='viewer',choices=[('admin','Администратор'),('manager','Менеджер'),('editor','Редактор шаблонов'),('viewer','Наблюдатель')])

class AppNotification(models.Model):
    key=models.CharField(max_length=160,unique=True)
    kind=models.CharField(max_length=16)
    code=models.CharField(max_length=32)
    title=models.CharField(max_length=180)
    message=models.CharField(max_length=600)
    visibility=models.CharField(max_length=16,default='all')
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True)
    object_type=models.CharField(max_length=24,blank=True)
    object_id=models.PositiveBigIntegerField(default=0)
    campaign_id=models.PositiveBigIntegerField(null=True)
    target=models.CharField(max_length=24,default='journal')
    audit_id=models.PositiveBigIntegerField(null=True)
    version=models.PositiveIntegerField(default=1)
    fingerprint=models.CharField(max_length=64,blank=True)
    resolved=models.BooleanField(default=False)
    created_at=models.DateTimeField(default=timezone.now)
    updated_at=models.DateTimeField(default=timezone.now,db_index=True)

class NotificationRead(models.Model):
    notification=models.ForeignKey(AppNotification,on_delete=models.CASCADE,related_name='receipts')
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    version=models.PositiveIntegerField()
    read_at=models.DateTimeField(default=timezone.now)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['notification','user'],name='notification_read_user_unique')]

class Audience(models.Model):
    name = models.CharField(max_length=120)
    name_key = models.CharField(max_length=240, unique=True, default='')
    parent = models.ForeignKey('self',null=True,blank=True,on_delete=models.PROTECT,related_name='children')
    created_at = models.DateTimeField(auto_now_add=True)
    def save(self,*args,**kwargs):
        self.name_key=str(self.parent_id or 'root')+':'+self.name.strip().casefold()
        super().save(*args,**kwargs)

class Client(models.Model):
    name = models.CharField(max_length=300)
    identity_key = models.CharField(max_length=400, unique=True)
    notes = models.TextField(blank=True)
    identifier = models.CharField(max_length=12,blank=True,db_index=True)
    city = models.CharField(max_length=160,blank=True)
    industry = models.CharField(max_length=200,blank=True)
    organization_type = models.CharField(max_length=200,blank=True)
    websites = models.JSONField(default=list)
    socials = models.JSONField(default=list)
    contact_people = models.JSONField(default=list)
    tags = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

class Address(models.Model):
    email = models.EmailField(max_length=254, unique=True)
    validation = models.CharField(max_length=30, default='Не проверен')
    validation_checked_at = models.DateTimeField(null=True,blank=True)
    validation_result = models.JSONField(default=dict)
    validation_attempts = models.PositiveSmallIntegerField(default=0)
    validation_next_at = models.DateTimeField(null=True,blank=True)
    archived = models.BooleanField(default=False)
    suppression_reason = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class EmailExclusion(models.Model):
    address = models.ForeignKey(Address, on_delete=models.PROTECT, related_name='exclusions')
    reason = models.CharField(max_length=30)
    note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    removal_note = models.CharField(max_length=500, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['address', 'reason'], condition=models.Q(removed_at__isnull=True), name='unique_active_email_exclusion')]

class EmailSubscription(models.Model):
    email = models.EmailField(max_length=254)
    scope = models.CharField(max_length=80, default='over_email_offers')
    is_test = models.BooleanField(default=True)
    state = models.CharField(max_length=16, default='pending')
    source = models.CharField(max_length=120)
    source_url = models.URLField(max_length=1000, blank=True)
    source_at = models.DateTimeField(default=timezone.now)
    evidence = models.CharField(max_length=1000, blank=True)
    consent_text = models.TextField()
    consent_version = models.CharField(max_length=40)
    generation = models.PositiveIntegerField(default=0)
    requested_at = models.DateTimeField(default=timezone.now)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['email','scope','is_test'], name='unique_email_subscription_scope')]


class SubscriptionEvent(models.Model):
    subscription = models.ForeignKey(EmailSubscription, on_delete=models.PROTECT, related_name='history')
    kind = models.CharField(max_length=20)
    source = models.CharField(max_length=120)
    snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class SubscriptionToken(models.Model):
    subscription = models.ForeignKey(EmailSubscription, on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64, unique=True)
    action = models.CharField(max_length=16)
    generation = models.PositiveIntegerField()
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class SavedSegment(models.Model):
    name=models.CharField(max_length=120)
    name_key=models.CharField(max_length=120,unique=True)
    filters=models.JSONField(default=dict)
    archived=models.BooleanField(default=False)
    updated_at=models.DateTimeField(auto_now=True)


class OverShopSubscription(models.Model):
    subscriber_id=models.CharField(max_length=30,unique=True)
    source_customer_id=models.CharField(max_length=30,blank=True)
    address=models.ForeignKey(Address,on_delete=models.PROTECT)
    client=models.ForeignKey(Client,on_delete=models.PROTECT,null=True,blank=True)
    active=models.BooleanField(default=False)
    confirmed=models.BooleanField(default=False)
    rubrics=models.JSONField(default=list)
    source_created_at=models.CharField(max_length=40,blank=True)
    source_updated_at=models.CharField(max_length=40,blank=True)
    source_confirmed_at=models.CharField(max_length=40,blank=True)
    source_hash=models.CharField(max_length=64)
    review_reason=models.CharField(max_length=200,blank=True)
    observed_at=models.DateTimeField(auto_now=True)


class EmailCheckState(models.Model):
    enabled=models.BooleanField(default=False)
    heartbeat_at=models.DateTimeField(null=True)
    lease=models.CharField(max_length=64,blank=True)
    lease_until=models.DateTimeField(null=True)
    error=models.CharField(max_length=300,blank=True)


class ClientFact(models.Model):
    client=models.ForeignKey(Client,on_delete=models.CASCADE,related_name='facts')
    field=models.CharField(max_length=40)
    value=models.JSONField()
    fingerprint=models.CharField(max_length=64)
    source=models.CharField(max_length=80)
    source_url=models.URLField(max_length=1000,blank=True)
    state=models.CharField(max_length=16,default='accepted')
    first_seen=models.DateTimeField(auto_now_add=True)
    last_seen=models.DateTimeField(auto_now=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['client','field','fingerprint','source','source_url'],name='unique_client_fact')]

class ProducerPilot(models.Model):
    id=models.PositiveSmallIntegerField(primary_key=True,default=1)
    token=models.UUIDField(null=True)
    enabled=models.BooleanField(default=False)
    cycle_seen=models.JSONField(default=dict)
    attempts=models.PositiveIntegerField(default=0)
    status=models.CharField(max_length=16,default='idle')
    page_limit=models.PositiveSmallIntegerField(default=2)
    next_page=models.PositiveIntegerField(default=1)
    rows=models.JSONField(default=list)
    issues=models.JSONField(default=list)
    report=models.JSONField(default=dict)
    result=models.JSONField(default=dict)
    error=models.CharField(max_length=300,blank=True)
    lease=models.CharField(max_length=64,blank=True)
    lease_until=models.DateTimeField(null=True)
    heartbeat_at=models.DateTimeField(null=True)
    next_attempt_at=models.DateTimeField(null=True)
    started_at=models.DateTimeField(null=True)
    completed_at=models.DateTimeField(null=True)

class Contact(models.Model):
    client = models.ForeignKey(Client, on_delete=models.PROTECT)
    address = models.ForeignKey(Address, on_delete=models.PROTECT,null=True,blank=True)
    audiences = models.ManyToManyField(Audience)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['client', 'address'], name='unique_client_address'),models.UniqueConstraint(fields=['client'],condition=models.Q(address__isnull=True),name='unique_client_no_email')]

class Evidence(models.Model):
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name='evidence')
    source = models.CharField(max_length=80)
    source_url = models.URLField(max_length=1000, blank=True)
    current = models.BooleanField(default=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['contact', 'source', 'source_url'], name='unique_contact_evidence')]

class ImportBatch(models.Model):
    token = models.UUIDField(unique=True)
    audience = models.ForeignKey(Audience, on_delete=models.PROTECT)
    rows = models.JSONField()
    errors = models.JSONField(default=list)
    committed_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    result = models.JSONField(default=dict)

class Phone(models.Model):
    client = models.ForeignKey(Client,on_delete=models.PROTECT,related_name='phones')
    number = models.CharField(max_length=40)
    raw = models.CharField(max_length=200)
    kind = models.CharField(max_length=12,default='unknown')
    source = models.CharField(max_length=80)
    source_url = models.URLField(max_length=1000,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['client','number','source','source_url'],name='unique_phone_evidence')]

class CollectionState(models.Model):
    id=models.PositiveSmallIntegerField(primary_key=True,default=1)
    enabled=models.BooleanField(default=False)
    lookback_days=models.PositiveSmallIntegerField(default=7)
    windows=models.JSONField(default=list)
    next_scan_at=models.DateTimeField(null=True)
    completed_at=models.DateTimeField(null=True)
    heartbeat_at=models.DateTimeField(null=True)
    next_request_at=models.DateTimeField(null=True)
    lease=models.CharField(max_length=64,blank=True)
    lease_until=models.DateTimeField(null=True)
    error=models.CharField(max_length=300,blank=True)

class CollectionItem(models.Model):
    external_id=models.PositiveBigIntegerField(unique=True)
    published_at=models.DateTimeField()
    state=models.CharField(max_length=16,default='queued',db_index=True)
    protocol_url=models.URLField(max_length=1000,blank=True)
    attempts=models.PositiveIntegerField(default=0)
    next_attempt_at=models.DateTimeField(default=timezone.now)
    error=models.CharField(max_length=300,blank=True)
    updated_at=models.DateTimeField(auto_now=True)

class CollectionParticipant(models.Model):
    identifier=models.CharField(max_length=12,unique=True)
    name=models.CharField(max_length=300)
    state=models.CharField(max_length=16,default='queued',db_index=True)
    attempts=models.PositiveIntegerField(default=0)
    next_attempt_at=models.DateTimeField(default=timezone.now)
    error=models.CharField(max_length=300,blank=True)
    updated_at=models.DateTimeField(auto_now=True)

class ParticipantOrigin(models.Model):
    participant=models.ForeignKey(CollectionParticipant,on_delete=models.CASCADE,related_name='origins')
    item=models.ForeignKey(CollectionItem,on_delete=models.CASCADE)
    protocol_url=models.URLField(max_length=1000)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['participant','item'],name='unique_participant_origin')]

class CollectionLog(models.Model):
    at=models.DateTimeField(auto_now_add=True)
    source=models.CharField(max_length=20,default='goszakup',db_index=True)
    level=models.CharField(max_length=12,default='info')
    message=models.CharField(max_length=300)

def gov_default_urls():
    return [f'https://www.gov.kz/org/{slug}?lang=ru' for slug in ('kazyna','edu','astana')]

class GovCollection(models.Model):
    id=models.PositiveSmallIntegerField(primary_key=True,default=1)
    urls=models.JSONField(default=gov_default_urls)
    enabled=models.BooleanField(default=False)
    cursor=models.PositiveIntegerField(default=0)
    report=models.JSONField(default=dict)
    seen=models.JSONField(default=dict)
    error=models.CharField(max_length=300,blank=True)
    attempts=models.PositiveIntegerField(default=0)
    next_attempt_at=models.DateTimeField(null=True)
    heartbeat_at=models.DateTimeField(null=True)
    completed_at=models.DateTimeField(null=True)
    lease=models.CharField(max_length=64,blank=True)
    lease_until=models.DateTimeField(null=True)

class DfoCollection(models.Model):
    id=models.PositiveSmallIntegerField(primary_key=True,default=1)
    enabled=models.BooleanField(default=False)
    mode=models.CharField(max_length=12,default='pilot')
    next_page=models.PositiveIntegerField(default=1)
    report=models.JSONField(default=dict)
    seen=models.JSONField(default=dict)
    error=models.CharField(max_length=300,blank=True)
    attempts=models.PositiveIntegerField(default=0)
    next_attempt_at=models.DateTimeField(null=True)
    heartbeat_at=models.DateTimeField(null=True)
    completed_at=models.DateTimeField(null=True)
    lease=models.CharField(max_length=64,blank=True)
    lease_until=models.DateTimeField(null=True)


class EmailTemplate(models.Model):
    design=models.JSONField(null=True,blank=True)
    name=models.CharField(max_length=160)
    subject=models.CharField(max_length=250,blank=True)
    preheader=models.CharField(max_length=250,blank=True)
    html=models.TextField(blank=True)
    revision=models.PositiveIntegerField(default=1)
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)


class CampaignSender(models.Model):
    name = models.CharField(max_length=160)
    email = models.EmailField(max_length=254, unique=True)
    reply_to = models.EmailField(max_length=254, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class EmailCampaign(models.Model):
    name = models.CharField(max_length=160)
    audiences = models.ManyToManyField(Audience)
    template = models.ForeignKey(EmailTemplate, on_delete=models.PROTECT)
    sender = models.ForeignKey(CampaignSender, on_delete=models.PROTECT)
    template_snapshot = models.JSONField(default=dict)
    sender_snapshot = models.JSONField(default=dict)
    recipient_counts = models.JSONField(default=dict)
    schedule_mode = models.CharField(max_length=16, default='manual')
    scheduled_at = models.DateTimeField(null=True, blank=True)
    schedule_timezone = models.CharField(max_length=64, default='Asia/Qyzylorda')
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class CampaignApproval(models.Model):
    campaign=models.OneToOneField(EmailCampaign,on_delete=models.CASCADE,related_name='approval')
    status=models.CharField(max_length=16,default='draft')
    version=models.PositiveIntegerField(default=0)
    campaign_revision=models.PositiveIntegerField(default=0)
    digest=models.CharField(max_length=64,blank=True)
    recipient_digest=models.CharField(max_length=64,blank=True)
    snapshot=models.JSONField(default=dict)
    submitted_by=models.CharField(max_length=180,blank=True)
    submitted_at=models.DateTimeField(null=True)
    reviewed_by=models.CharField(max_length=180,blank=True)
    reviewed_at=models.DateTimeField(null=True)
    comment=models.TextField(blank=True)


class CampaignQueue(models.Model):
    campaign = models.OneToOneField(EmailCampaign, on_delete=models.PROTECT, related_name='dispatch_queue')
    state = models.CharField(max_length=16, default='paused', db_index=True)
    snapshot = models.JSONField(default=dict)
    rate_per_minute = models.PositiveSmallIntegerField(default=30)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    retry_seconds = models.PositiveIntegerField(default=60)
    scenario = models.CharField(max_length=30, default='success')
    ready_at = models.DateTimeField(default=timezone.now)
    next_slot_at = models.DateTimeField(null=True, blank=True)
    last_processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class CampaignQueueItem(models.Model):
    queue = models.ForeignKey(CampaignQueue, on_delete=models.CASCADE, related_name='items')
    address = models.ForeignKey(Address, on_delete=models.PROTECT)
    email = models.EmailField(max_length=254)
    state = models.CharField(max_length=16, default='pending')
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    detail = models.CharField(max_length=300, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['queue','email'], name='unique_campaign_queue_email')]
        indexes = [models.Index(fields=['queue','state','next_attempt_at'], name='campaign_queue_due')]


class CampaignQueueAttempt(models.Model):
    item = models.ForeignKey(CampaignQueueItem, on_delete=models.CASCADE, related_name='history')
    number = models.PositiveSmallIntegerField()
    outcome = models.CharField(max_length=16)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['item','number'], name='unique_campaign_queue_attempt')]


class CampaignTestEvent(models.Model):
    event_id = models.UUIDField(unique=True)
    campaign = models.ForeignKey(EmailCampaign, null=True, blank=True, on_delete=models.PROTECT, related_name='test_events')
    recipient = models.EmailField(max_length=254)
    kind = models.CharField(max_length=20)
    occurred_at = models.DateTimeField(default=timezone.now)
    link = models.URLField(max_length=2000, blank=True)
    error_type = models.CharField(max_length=16, blank=True)
    detail = models.CharField(max_length=500, blank=True)
    fingerprint = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        indexes = [models.Index(fields=['campaign','occurred_at'], name='campaign_test_event_period'),
                   models.Index(fields=['campaign','kind','recipient'], name='campaign_test_event_kind')]


class TrackedMessage(models.Model):
    """Prepared mail only. Creating a record does not claim SMTP acceptance."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(EmailCampaign, null=True, blank=True, on_delete=models.PROTECT)
    template = models.ForeignKey(EmailTemplate, on_delete=models.PROTECT)
    recipient = models.EmailField(max_length=254)
    is_test = models.BooleanField(default=True, db_index=True)
    state = models.CharField(max_length=16, default='preview')
    subject = models.CharField(max_length=250)
    preparation_snapshot = models.JSONField(default=dict)
    unsubscribe_token = models.OneToOneField(SubscriptionToken, on_delete=models.PROTECT)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)


class TrackingLink(models.Model):
    message = models.ForeignKey(TrackedMessage, on_delete=models.CASCADE, related_name='links')
    token_hash = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=10)
    target = models.URLField(max_length=2000, blank=True)


class TrackingEvent(models.Model):
    message = models.ForeignKey(TrackedMessage, on_delete=models.CASCADE, related_name='events')
    link = models.ForeignKey(TrackingLink, null=True, blank=True, on_delete=models.PROTECT)
    kind = models.CharField(max_length=20)
    occurred_at = models.DateTimeField(default=timezone.now)
    class Meta:
        # One first observation per link. Retries, proxies and reloads don't inflate totals.
        constraints = [models.UniqueConstraint(fields=['message','kind','link'], name='unique_tracking_link_event'),
                       models.UniqueConstraint(fields=['message','kind'], condition=models.Q(kind='unsubscribe'), name='unique_message_unsubscribe')]
        indexes = [models.Index(fields=['message','kind'], name='tracking_message_kind')]


class ActivityTestEvent(models.Model):
    event_id = models.UUIDField(unique=True)
    recipient = models.EmailField(max_length=254)
    kind = models.CharField(max_length=20)
    message_key = models.CharField(max_length=120, blank=True)
    error_type = models.CharField(max_length=16, blank=True)
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        indexes = [models.Index(fields=['recipient','occurred_at'], name='activity_test_recipient_time')]


class AutomationScenario(models.Model):
    name = models.CharField(max_length=160)
    revision = models.PositiveIntegerField(default=1)
    config = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class AutomationSimulationClock(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    clock_at = models.DateTimeField(default=timezone.now)


class AutomationSimulationRun(models.Model):
    scenario = models.ForeignKey(AutomationScenario, on_delete=models.PROTECT, related_name='runs')
    state = models.CharField(max_length=16, default='running')
    snapshot = models.JSONField(default=dict)
    started_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['scenario'],condition=models.Q(state__in=['running','paused']),name='one_open_automation_simulation')]


class AutomationSimulationMember(models.Model):
    run = models.ForeignKey(AutomationSimulationRun, on_delete=models.CASCADE, related_name='members')
    recipient = models.EmailField(max_length=254)
    state = models.CharField(max_length=16, default='waiting')
    step_index = models.PositiveSmallIntegerField(default=0)
    entered_at = models.DateTimeField()
    next_at = models.DateTimeField()
    detail = models.CharField(max_length=500, blank=True)
    frequency_base_at = models.DateTimeField(null=True, blank=True)
    frequency_revision = models.PositiveIntegerField(default=0)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['run','recipient'],name='unique_simulation_member')]
        indexes = [models.Index(fields=['run','state','next_at'],name='automation_simulation_due')]


class AutomationSimulationAction(models.Model):
    member = models.ForeignKey(AutomationSimulationMember, on_delete=models.PROTECT, related_name='actions')
    campaign = models.ForeignKey(EmailCampaign, on_delete=models.PROTECT)
    recipient = models.EmailField(max_length=254, db_index=True)
    step_index = models.PositiveSmallIntegerField()
    occurred_at = models.DateTimeField()
    snapshot = models.JSONField(default=dict)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['member','step_index'],name='unique_simulation_step_action')]
        indexes = [models.Index(fields=['recipient','occurred_at'],name='simulation_recipient_time')]


class AutomationSimulationLog(models.Model):
    run = models.ForeignKey(AutomationSimulationRun, on_delete=models.CASCADE, related_name='logs')
    recipient = models.EmailField(max_length=254, blank=True)
    kind = models.CharField(max_length=30)
    detail = models.CharField(max_length=500)
    occurred_at = models.DateTimeField()


class AccountAccessGate(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    setup_digest = models.CharField(max_length=64, blank=True)
    setup_expires_at = models.DateTimeField(null=True, blank=True)


class AuthenticationLimit(models.Model):
    key = models.CharField(max_length=64, unique=True)
    failures = models.PositiveIntegerField(default=0)
    window_at = models.DateTimeField()


class ActionAuditEntry(models.Model):
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor_key = models.CharField(max_length=160, db_index=True)
    actor_label = models.CharField(max_length=180)
    actor_source = models.CharField(max_length=24)
    initiated_by = models.JSONField(default=dict)
    action = models.CharField(max_length=32, db_index=True)
    object_type = models.CharField(max_length=24)
    object_id = models.PositiveBigIntegerField(default=0)
    object_label = models.CharField(max_length=180, blank=True)
    result = models.CharField(max_length=16, default='success', db_index=True)
    summary = models.TextField()
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    request_id = models.UUIDField(null=True, blank=True, db_index=True)
    class Meta:
        indexes = [models.Index(fields=['object_type','object_id','occurred_at'],name='audit_object_time')]


class ActionAuditCampaign(models.Model):
    entry = models.ForeignKey(ActionAuditEntry, on_delete=models.CASCADE, related_name='campaign_refs')
    campaign_id = models.PositiveBigIntegerField(db_index=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['entry','campaign_id'],name='unique_action_campaign_ref')]


class TestFrequencyPolicy(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    enabled = models.BooleanField(default=True)
    min_interval_minutes = models.PositiveIntegerField(default=1440)
    max_24h = models.PositiveIntegerField(default=1)
    max_7d = models.PositiveIntegerField(default=3)
    revision = models.PositiveIntegerField(default=0)
    clock_at = models.DateTimeField(default=timezone.now)


class TestFrequencyChange(models.Model):
    revision = models.PositiveIntegerField(unique=True)
    snapshot = models.JSONField(default=dict)
    clock_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)


class CampaignRehearsal(models.Model):
    scenario = models.ForeignKey(AutomationScenario, on_delete=models.PROTECT)
    snapshot = models.JSONField(default=dict)
    baseline = models.JSONField(default=list)
    members = models.JSONField(default=list)
    state = models.CharField(max_length=16, default='running')
    clock_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)


class RehearsalLetter(models.Model):
    rehearsal = models.ForeignKey(CampaignRehearsal, on_delete=models.PROTECT, related_name='letters')
    message = models.OneToOneField(TrackedMessage, on_delete=models.PROTECT, related_name='rehearsal_letter')
    recipient = models.EmailField()
    step_index = models.PositiveSmallIntegerField()
    prepared_at = models.DateTimeField()
    checks = models.JSONField(default=dict)
    preview_html = models.TextField()
    delivery_state = models.CharField(max_length=24, default='untested')
    attempt_count = models.PositiveSmallIntegerField(default=1)
    retry_at = models.DateTimeField(null=True, blank=True)
    delivery_detail = models.CharField(max_length=500, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['rehearsal','recipient','step_index'], name='unique_rehearsal_letter')]
        indexes = [models.Index(fields=['recipient','prepared_at'],name='rehearsal_frequency_email')]


class RehearsalDeliveryEvent(models.Model):
    event_id = models.UUIDField(unique=True)
    letter = models.ForeignKey(RehearsalLetter, on_delete=models.PROTECT, related_name='delivery_events')
    attempt = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=24)
    detail = models.CharField(max_length=500, blank=True)
    occurred_at = models.DateTimeField()
    class Meta:
        constraints = [models.UniqueConstraint(fields=['letter','attempt','kind'], name='unique_rehearsal_delivery_event')]


class RehearsalObservation(models.Model):
    rehearsal = models.ForeignKey(CampaignRehearsal, on_delete=models.PROTECT, related_name='observations')
    event = models.OneToOneField(TrackingEvent, on_delete=models.PROTECT)
    virtual_at = models.DateTimeField()


class RehearsalLog(models.Model):
    rehearsal = models.ForeignKey(CampaignRehearsal, on_delete=models.PROTECT, related_name='logs')
    recipient = models.EmailField(blank=True)
    kind = models.CharField(max_length=30)
    detail = models.TextField()
    occurred_at = models.DateTimeField()


class CampaignQueueWorker(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    heartbeat_at = models.DateTimeField(null=True)


class TemplateAIQuota(models.Model):
    id=models.PositiveSmallIntegerField(primary_key=True,default=1)
    day=models.DateField(null=True)
    calls=models.PositiveIntegerField(default=0)
    next_at=models.DateTimeField(null=True)


class TemplateTestMailGate(models.Model):
    id=models.PositiveSmallIntegerField(primary_key=True,default=1)
    next_at=models.DateTimeField(null=True)


class TemplateTestMail(models.Model):
    token=models.UUIDField(primary_key=True)
    fingerprint=models.CharField(max_length=64)
    recipient=models.EmailField()
    subject=models.CharField(max_length=270)
    state=models.CharField(max_length=16,default='pending')
    detail=models.CharField(max_length=300,blank=True)
    message_id=models.CharField(max_length=250,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)


class KaspiCollection(models.Model):
    id=models.PositiveSmallIntegerField(primary_key=True,default=1)
    enabled=models.BooleanField(default=False)
    city_code=models.CharField(max_length=20,default='750000000')
    cycle=models.PositiveIntegerField(default=1)
    next_request_at=models.DateTimeField(null=True)
    completed_at=models.DateTimeField(null=True)
    heartbeat_at=models.DateTimeField(null=True)
    lease=models.CharField(max_length=64,blank=True)
    lease_until=models.DateTimeField(null=True)
    error=models.CharField(max_length=300,blank=True)
    report=models.JSONField(default=dict)


class KaspiMerchant(models.Model):
    merchant_id=models.CharField(max_length=80,unique=True)
    name=models.CharField(max_length=300)
    city=models.CharField(max_length=160,blank=True)
    addresses=models.JSONField(default=list)
    phones=models.JSONField(default=list)
    category_codes=models.JSONField(default=list)
    source_urls=models.JSONField(default=list)
    client=models.ForeignKey(Client,on_delete=models.PROTECT,null=True)
    checked_at=models.DateTimeField(null=True)


class KaspiTask(models.Model):
    cycle=models.PositiveIntegerField(default=1)
    kind=models.CharField(max_length=16)
    key=models.CharField(max_length=200)
    priority=models.PositiveSmallIntegerField(default=20)
    payload=models.JSONField(default=dict)
    state=models.CharField(max_length=16,default='queued',db_index=True)
    attempts=models.PositiveSmallIntegerField(default=0)
    next_at=models.DateTimeField(default=timezone.now)
    error=models.CharField(max_length=300,blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['cycle','kind','key'],name='kaspi_unique_task')]

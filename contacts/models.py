from django.db import models
from django.utils import timezone

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

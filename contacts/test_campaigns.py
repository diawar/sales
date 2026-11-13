from django.test import override_settings as _legacy_auth
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from .models import Audience, Address, Client, Contact, EmailTemplate, CampaignSender, EmailCampaign


@_legacy_auth(SALES_AUTH_REQUIRED=False)
class CampaignTests(TestCase):
    def setUp(self):
        self.root=Audience.objects.create(name='Business')
        self.child=Audience.objects.create(name='Computers',parent=self.root)
        self.grandchild=Audience.objects.create(name='Parts',parent=self.child)
        self.other=Audience.objects.create(name='Other')
        self.template=EmailTemplate.objects.create(name='Offer',subject='News',html='<p>News</p><a href="{{unsubscribe_url}}">Unsubscribe</a>')
        self.sender=CampaignSender.objects.create(name='OVER',email='news@example.kz')
        self.address=Address.objects.create(email='good@example.com',validation_result={'code':'mx'})
        self.add_contact('one',self.address,self.child,self.root)
        self.add_contact('two',self.address,self.grandchild)
        self.add_contact('missing',None,self.root)
        for label,fields in [('archived',{'archived':True}),('suppressed',{'suppression_reason':'Отписка'}),('invalid',{'validation_result':{'code':'null_mx'}}),('pending',{'validation_result':{'code':'temporary'}})]:
            self.add_contact(label,Address.objects.create(email=label+'@example.com',**fields),self.child)
        self.add_contact('other',Address.objects.create(email='other@example.com',validation_result={'code':'mx'}),self.other)
        self.data={'name':'Campaign','audience_ids':[self.root.pk,self.child.pk], 'template_id':self.template.pk,
                   'template_revision':self.template.revision,'sender_id':self.sender.pk, 'schedule_mode':'manual','schedule_timezone':'Asia/Qyzylorda'}

    def add_contact(self,name,address,*audiences):
        row=Contact.objects.create(client=Client.objects.create(name=name,identity_key=name),address=address)
        row.audiences.add(*audiences)
        return row

    def post(self,url,data):return self.client.post(url,data=data,content_type='application/json')

    def test_counts_include_descendants_deduplicate_and_partition(self):
        response=self.post('/api/campaigns/recipients',self.data)
        self.assertEqual(response.status_code,200)
        counts=response.json()
        self.assertEqual(counts['contacts'],7)
        self.assertEqual(counts['unique_emails'],5)
        self.assertEqual(counts['duplicates'],1)
        self.assertEqual(counts['without_email'],1)
        for key in ('ready','archived','suppressed','invalid','pending'):self.assertEqual(counts[key],1)
        self.assertEqual(sum(counts[k] for k in ('ready','archived','suppressed','invalid','pending')),counts['unique_emails'])

    def test_case_variant_cannot_bypass_restriction(self):
        variant=Address.objects.create(email='GOOD@EXAMPLE.COM',suppression_reason='Жалоба')
        self.add_contact('case',variant,self.other)
        counts=self.post('/api/campaigns/recipients',self.data).json()
        self.assertEqual(counts['ready'],0)
        self.assertEqual(counts['suppressed'],2)
        self.assertEqual(counts['unique_emails'],5)

    def test_save_reload_snapshot_revision_and_recalculate(self):
        response=self.post('/api/campaigns',self.data)
        self.assertEqual(response.status_code,201)
        saved=response.json();url=f"/api/campaigns/{saved['id']}"
        self.assertEqual(saved['status'],'draft')
        self.assertEqual(saved['template_snapshot']['html'],self.template.html)
        self.assertEqual(self.client.get(url).json()['sender_snapshot']['email'],self.sender.email)
        self.address.suppression_reason='Отписка';self.address.save()
        self.data['revision']=saved['revision']
        response=self.post(url,self.data)
        self.assertEqual(response.json()['recipient_counts']['ready'],0)
        self.assertEqual(self.post(url,self.data).status_code,409)
        self.template.subject='Changed';self.template.save()
        self.assertEqual(EmailCampaign.objects.get().template_snapshot['subject'],'News')
        self.assertEqual(self.client.get('/api/campaigns').json()['total'],1)

    def test_schedule_conversion_and_past_invalid_date_rejected(self):
        local=(timezone.now()+timedelta(days=3)).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Qyzylorda')).replace(second=0,microsecond=0,tzinfo=None)
        self.data.update(schedule_mode='scheduled',scheduled_at=local.isoformat())
        response=self.post('/api/campaigns',self.data)
        self.assertEqual(response.status_code,201)
        row=EmailCampaign.objects.get()
        self.assertEqual(row.scheduled_at.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Qyzylorda')).replace(tzinfo=None),local)
        for value in ['2020-01-01T10:00','2026-02-30T10:00','bad',None]:
            self.assertEqual(self.post('/api/campaigns/preview',{**self.data,'scheduled_at':value}).status_code,400)

    def test_invalid_data_missing_entities_and_stale_template(self):
        for data in [{},{**self.data,'audience_ids':[]},{**self.data,'audience_ids':[True]},{**self.data,'audience_ids':[999999]},
                     {**self.data,'name':'x'*161},{**self.data,'template_id':999999},{**self.data,'sender_id':999999},
                     {**self.data,'schedule_timezone':'bad'},{**self.data,'template_revision':99}]:
            self.assertEqual(self.post('/api/campaigns',data).status_code,400)
        self.assertEqual(self.client.get('/api/campaigns/99999').status_code,404)
        self.assertEqual(EmailCampaign.objects.count(),0)

    def test_zero_recipients_and_bad_template_stay_draft_without_sending(self):
        self.template.html='';self.template.save()
        self.data['audience_ids']=[self.other.pk]
        Address.objects.filter(email='other@example.com').update(suppression_reason='Запрет')
        with patch('django.core.mail.EmailMultiAlternatives.send') as send:
            preview=self.post('/api/campaigns/preview',self.data).json()
            self.assertEqual(preview['counts']['ready'],0)
            self.assertGreater(preview['checks']['errors'],0)
            self.assertEqual(self.post('/api/campaigns',self.data).status_code,201)
            send.assert_not_called()

    def test_sender_validation_normalization_and_duplicates(self):
        response=self.post('/api/campaign-senders',{'name':'Store','email':' INFO@EXAMPLE.KZ ','reply_to':'reply@example.kz'})
        self.assertEqual(response.status_code,201)
        self.assertEqual(response.json()['email'],'info@example.kz')
        self.assertEqual(self.post('/api/campaign-senders',{'name':'Store','email':'info@example.kz'}).status_code,409)
        for fields in [{'name':'bad\nname','email':'x@example.com'},{'name':'Name','email':'bad'},{'name':'Name','email':'ok@example.com','reply_to':'bad'}]:
            self.assertEqual(self.post('/api/campaign-senders',fields).status_code,400)
        self.assertFalse(self.client.get('/api/campaign-senders').json()['transport_connected'])

from django.test import override_settings as _legacy_auth
from datetime import timedelta
from urllib.parse import urlsplit
from unittest.mock import patch
from django.test import TestCase, Client as HttpClient, override_settings
from django.utils import timezone
from .models import (Audience, Address, Client, Contact, EmailTemplate, CampaignSender,
                     EmailCampaign, CampaignQueue, CampaignQueueItem, TrackedMessage, TrackingEvent,
                     EmailSubscription, SubscriptionToken, EmailExclusion, CampaignTestEvent)


@override_settings(SALES_PUBLIC_BASE_URL='https://sales.example.com')
@_legacy_auth(SALES_AUTH_REQUIRED=False)
class CampaignMailCheckTests(TestCase):
    def setUp(self):
        self.audience=Audience.objects.get_or_create(name='Бизнес',parent=None)[0]
        self.child=Audience.objects.create(name='Компьютеры',parent=self.audience)
        self.other_audience=Audience.objects.create(name='Другие')
        self.template=EmailTemplate.objects.create(name='Original',subject='Saved {{name}}',preheader='News',
            html='<p>{{company}}: {{city|не указан}}</p><a href="https://shop.example.com/old">Offer</a><a href="{{unsubscribe_url}}">Unsubscribe</a>')
        self.sender=CampaignSender.objects.create(name='Saved sender',email='saved@example.com',reply_to='reply@example.com')
        self.campaign=self.make_campaign('First')
        self.second=self.make_campaign('Second')
        self.address=Address.objects.create(email='client@example.com',validation_result={'code':'mx'})
        self.contact=self.make_contact('Company',self.address,self.child)
        self.outside=self.make_contact('Outside',Address.objects.create(email='outside@example.com'),self.other_audience)
        self.url=f'/api/campaigns/{self.campaign.pk}/mail-check'

    def make_campaign(self,name):
        c=EmailCampaign.objects.create(name=name,template=self.template,sender=self.sender,
            template_snapshot={k:getattr(self.template,k) for k in ('id','name','revision','subject','preheader','html')},
            sender_snapshot={k:getattr(self.sender,k) for k in ('id','name','email','reply_to')})
        c.audiences.add(self.audience)
        return c

    def make_contact(self,name,address,audience):
        c=Contact.objects.create(client=Client.objects.create(name=name,identity_key=name,city='Алматы'),address=address)
        c.audiences.add(audience);return c

    def data(self,**changes):
        return {'version':self.client.get(self.url).json()['version'],'action':'prepare','recipient':'preview@example.invalid',
                'values':{'name':'Buyer','company':'Company','city':'Алматы'},'is_test':True,'send':False,**changes}

    def post(self,**changes):return self.client.post(self.url,self.data(**changes),content_type='application/json')

    def path(self,url):return urlsplit(url).path

    def test_get_uses_saved_snapshots_and_does_not_change_data(self):
        self.template.subject='Changed live template';self.template.save()
        self.sender.email='changed@example.com';self.sender.save()
        r=self.client.get(self.url);self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['template']['subject'],'Saved {{name}}')
        self.assertEqual(r.json()['sender']['email'],'saved@example.com')
        self.assertEqual(r.json()['clients'][0]['id'],self.contact.pk)
        self.assertEqual(r.json()['clients'][0]['values']['company'],'Company')
        self.assertEqual(TrackedMessage.objects.count(),0);self.assertEqual(SubscriptionToken.objects.count(),0)

    def test_prepare_preserves_campaign_template_and_sender_without_network_or_send(self):
        self.template.html='<p>New live HTML</p>';self.template.save()
        with patch('httpx.Client.request') as network,patch('django.core.mail.message.EmailMessage.send') as send:
            r=self.post(contact_id=self.contact.pk)
        self.assertEqual(r.status_code,201);network.assert_not_called();send.assert_not_called()
        data=r.json();message=TrackedMessage.objects.get()
        self.assertEqual(message.campaign_id,self.campaign.pk)
        self.assertEqual(data['subject'],'Saved Buyer');self.assertEqual(data['headers']['From'],'Saved sender <saved@example.com>')
        self.assertEqual(data['headers']['Reply-To'],'reply@example.com')
        self.assertEqual(data['preparation_snapshot']['contact_id'],self.contact.pk)
        self.assertEqual(data['preparation_snapshot']['basis'],'campaign')
        self.assertEqual(message.links.get(kind='click').target,'https://shop.example.com/old')
        self.assertEqual(TrackingEvent.objects.count(),0);self.assertFalse(data['sent'])

    def test_queue_snapshot_takes_priority_over_campaign_snapshot(self):
        queue=CampaignQueue.objects.create(campaign=self.campaign,snapshot={'campaign_revision':1,
            'template':self.campaign.template_snapshot|{'subject':'Frozen queue subject'},
            'sender':self.campaign.sender_snapshot|{'email':'queue@example.com'}})
        self.campaign.template_snapshot['subject']='Other draft subject';self.campaign.revision=2;self.campaign.save()
        r=self.post();self.assertEqual(r.status_code,201)
        self.assertEqual(r.json()['subject'],'Frozen queue subject')
        self.assertEqual(r.json()['headers']['From'],'Saved sender <queue@example.com>')
        self.assertEqual(r.json()['preparation_snapshot']['basis'],'queue')
        self.assertEqual(r.json()['preparation_snapshot']['version']['queue_id'],queue.pk)

    def test_stale_draft_or_new_queue_requires_refresh_without_creating_tokens(self):
        data=self.data();self.campaign.revision+=1;self.campaign.save()
        self.assertEqual(self.client.post(self.url,data,content_type='application/json').status_code,409)
        data=self.data();CampaignQueue.objects.create(campaign=self.campaign,snapshot={'template':self.campaign.template_snapshot,'sender':self.campaign.sender_snapshot})
        self.assertEqual(self.client.post(self.url,data,content_type='application/json').status_code,409)
        self.assertEqual(SubscriptionToken.objects.count(),0)

    def test_diagnosis_lists_missing_personalization_and_never_creates_records(self):
        r=self.post(action='diagnose',values={'name':'Buyer','city':''})
        self.assertEqual(r.status_code,200);self.assertFalse(r.json()['can_prepare'])
        self.assertEqual(r.json()['missing'],['company'])
        self.assertEqual(EmailSubscription.objects.count(),0)
        r=self.post(values={'name':'Buyer','city':''})
        self.assertEqual(r.status_code,400);self.assertIn('diagnosis',r.json())
        self.assertEqual(TrackedMessage.objects.count(),0)

    def test_dynamic_links_and_sender_headers_checked_after_substitution(self):
        self.campaign.template_snapshot['html']='<p>{{company}}</p><a href="{{city}}">Link</a><a href="{{unsubscribe_url}}">Unsubscribe</a>'
        self.campaign.sender_snapshot['name']='Bad\r\nBcc: x@example.com';self.campaign.save()
        r=self.post(action='diagnose',values={'name':'Buyer','company':'Company','city':'javascript:alert(1)'})
        self.assertEqual(r.status_code,200)
        fails={c['code'] for c in r.json()['checks'] if c['status']=='fail'}
        self.assertIn('sender',fails);self.assertIn('links',fails)
        self.assertEqual(self.post().status_code,400)

    def test_contacts_are_campaign_scoped_searchable_and_bounded(self):
        r=self.client.get(self.url,{'query':'outside'}).json();self.assertEqual(r['clients'],[])
        self.assertEqual(self.post(contact_id=self.outside.pk).status_code,400)
        for i in range(25):self.make_contact('More '+str(i),None,self.child)
        r=self.client.get(self.url).json();self.assertEqual(len(r['clients']),20);self.assertTrue(r['clients_more'])
        self.assertEqual(len(self.client.get(self.url,{'query':'client@example.com'}).json()['clients']),1)

    def test_queue_limits_client_picker_to_frozen_addresses(self):
        second=self.make_contact('Later',Address.objects.create(email='later@example.com'),self.child)
        q=CampaignQueue.objects.create(campaign=self.campaign,snapshot={'template':self.campaign.template_snapshot,'sender':self.campaign.sender_snapshot})
        CampaignQueueItem.objects.create(queue=q,address=self.address,email=self.address.email)
        r=self.client.get(self.url).json()
        self.assertEqual([c['id'] for c in r['clients']],[self.contact.pk])
        self.assertEqual(self.post(contact_id=second.pk).status_code,400)

    def test_individuals_have_no_company_value(self):
        physical=Audience.objects.get_or_create(name='Физические лица',parent=None)[0]
        self.campaign.audiences.add(physical)
        c=self.make_contact('Person',None,physical)
        data=self.client.get(self.url,{'query':'Person'}).json()['clients'][0]
        self.assertEqual(data['id'],c.pk);self.assertEqual(data['values']['company'],'');self.assertEqual(data['email'],'')

    def test_campaign_statistics_exclude_other_campaigns_unlinked_and_synthetic(self):
        first=self.post().json()
        second=self.client.post(f'/api/campaigns/{self.second.pk}/mail-check',self.data(),content_type='application/json').json()
        unlinked=self.client.post('/api/mail-tracking/preview',{'template_id':self.template.pk,'recipient':'preview@example.invalid',
            'values':{'name':'Buyer','company':'Company','city':'City'}},content_type='application/json').json()
        for data in (first,second,unlinked):self.client.get(self.path(data['pixel_url']))
        self.client.get(self.path(first['clicks'][0]['url']))
        self.client.post(self.path(first['unsubscribe_url']))
        import uuid
        CampaignTestEvent.objects.create(event_id=uuid.uuid4(),campaign=self.campaign,recipient='preview@example.invalid',kind='open',fingerprint='synthetic')
        r=self.client.get('/api/mail-tracking',{'campaign_id':self.campaign.pk}).json()
        self.assertEqual(r['campaign_name'],'First');self.assertEqual(r['messages_count'],1)
        self.assertEqual(r['totals'],{'open':1,'click':1,'unsubscribe':1})
        self.assertTrue(all(e['campaign_id']==self.campaign.pk for e in r['history']))
        self.assertEqual(self.client.get('/api/mail-tracking',{'campaign_id':self.second.pk,'message_id':first['id']}).json()['messages_count'],0)
        self.assertEqual(self.client.get('/api/mail-tracking',{'campaign_id':self.campaign.pk,'mode':'real'}).json()['history'],[])
        self.address.refresh_from_db();self.assertEqual(self.address.suppression_reason,'')
        self.assertEqual(EmailExclusion.objects.count(),0)

    def test_statistics_snapshot_survives_later_campaign_changes_and_dates_filter_events(self):
        data=self.post().json();self.client.get(self.path(data['pixel_url']))
        self.campaign.revision=8;self.campaign.sender_snapshot['name']='New';self.campaign.save()
        r=self.client.get('/api/mail-tracking',{'campaign_id':self.campaign.pk}).json()
        self.assertEqual(r['history'][0]['preparation_snapshot']['version']['campaign_revision'],1)
        self.assertEqual(r['history'][0]['preparation_snapshot']['sender']['name'],'Saved sender')
        tomorrow=(timezone.localdate()+timedelta(days=1)).isoformat()
        self.assertEqual(self.client.get('/api/mail-tracking',{'campaign_id':self.campaign.pk,'from':tomorrow}).json()['totals']['open'],0)

    def test_invalid_campaign_period_and_permissions_do_not_create_real_messages(self):
        for params in ({'campaign_id':'no'},{'campaign_id':'9999999999'},{'from':'bad'},{'to':'9999-01-01'},{'from':'2026-02-02','to':'2026-01-01'}):
            self.assertIn(self.client.get('/api/mail-tracking',params).status_code,(400,404))
        self.assertEqual(self.post(is_test=False).status_code,400);self.assertEqual(self.post(send=True).status_code,400)
        self.assertEqual(self.post(contact_id=True).status_code,400)
        self.assertEqual(self.post(action='unknown').status_code,400)
        browser=HttpClient(enforce_csrf_checks=True)
        self.assertEqual(browser.post(self.url,self.data(),content_type='application/json').status_code,403)
        self.assertEqual(TrackedMessage.objects.count(),0)

    @override_settings(SALES_PUBLIC_BASE_URL='http://public.example.com')
    def test_invalid_public_origin_blocks_preparation_but_diagnosis_is_read_only(self):
        r=self.post(action='diagnose');self.assertFalse(r.json()['can_prepare'])
        self.assertEqual(self.post().status_code,400);self.assertEqual(SubscriptionToken.objects.count(),0)

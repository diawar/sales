from django.test import override_settings as _legacy_auth
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase,override_settings
from django.utils import timezone
from .models import Audience,Client,Contact,Address,EmailTemplate,CampaignSender,EmailCampaign,OverShopSubscription,EmailExclusion,CampaignQueue,CampaignQueueItem,CampaignTestEvent


@_legacy_auth(SALES_AUTH_REQUIRED=False)
class CampaignReadinessTests(TestCase):
    def setUp(self):
        self.audience=Audience.objects.get_or_create(name_key='root:бизнес',defaults={'name':'Бизнес'})[0]
        self.person=Client.objects.create(name='Alpha',identity_key='alpha',city='Алматы')
        self.address=Address.objects.create(email='alpha@example.com',validation_result={'code':'mx'},validation_checked_at=timezone.now())
        self.contact=Contact.objects.create(client=self.person,address=self.address);self.contact.audiences.add(self.audience)
        self.template=EmailTemplate.objects.create(name='Template',subject='Offer {{name}}',preheader='Details',html='<meta name="viewport" content="width=device-width"><p>Hello {{name}}, {{city|вашем городе}}</p><a href="https://over-shop.kz/">Shop</a><a href="{{unsubscribe_url}}">Unsubscribe</a>')
        self.sender=CampaignSender.objects.create(name='OVER',email='news@overclockers.kz')
        self.campaign=EmailCampaign.objects.create(name='Campaign',template=self.template,sender=self.sender,
            template_snapshot={k:getattr(self.template,k) for k in ('id','name','subject','preheader','html','revision')},
            sender_snapshot={'name':self.sender.name,'email':self.sender.email,'reply_to':''})
        self.campaign.audiences.add(self.audience)
        self.url=f'/api/campaigns/{self.campaign.pk}/readiness'

    def confirm(self,**values):
        data=dict(subscriber_id='1',address=self.address,active=True,confirmed=True,rubrics=['offers'],source_hash='evidence',source_confirmed_at=timezone.now().isoformat())
        return OverShopSubscription.objects.create(**(data|values))
    def result(self):
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,200,response.content)
        return response.json()
    def check(self,result,code):return next(c for c in result['checks'] if c['code']==code)
    def html(self,value):
        self.campaign.template_snapshot['html']=value;self.campaign.save()

    def test_prepared_content_still_never_allows_real_send(self):
        self.confirm();result=self.result()
        self.assertFalse(result['can_send'])
        self.assertEqual(result['verdict'],'waiting_integration')
        self.assertEqual(result['recipients']['technical_ready'],1)
        self.assertEqual(result['consent']['confirmed'],1)
        for code in ('transport','sender_domain','consent_scope'):
            self.assertEqual(self.check(result,code)['status'],'pending')

    def test_registration_and_public_email_are_not_consent(self):
        result=self.result()
        self.assertEqual(result['consent']['unknown'],1)
        self.assertEqual(self.check(result,'consent')['status'],'fail')
        self.assertEqual(result['verdict'],'needs_changes')

    def test_subscription_needs_evidence_and_inactive_is_not_revocation(self):
        row=self.confirm(active=False)
        result=self.result();self.assertEqual(result['consent']['unknown'],1);self.assertEqual(result['consent']['revoked'],0)
        for field,value in [('active',True),('source_confirmed_at','')]:setattr(row,field,value)
        row.save();self.assertEqual(self.result()['consent']['unknown'],1)
        row.source_confirmed_at='2026-01-01';row.review_reason='review';row.save()
        self.assertEqual(self.result()['consent']['unknown'],1)

    def test_revocation_dominates_positive_subscription(self):
        self.confirm()
        EmailExclusion.objects.create(address=self.address,reason='unsubscribe')
        Address.objects.filter(pk=self.address.pk).update(suppression_reason='Отписка')
        result=self.result()
        self.assertEqual(result['consent']['revoked'],1)
        self.assertEqual(result['consent']['confirmed'],0)
        self.assertEqual(result['recipients']['technical_ready'],0)

    def test_dns_exclusion_is_recalculated_each_request(self):
        self.assertEqual(self.result()['recipients']['technical_ready'],1)
        self.address.validation_result={'code':'temporary'};self.address.save()
        result=self.result();self.assertEqual(result['recipients']['technical_ready'],0)
        self.assertEqual(self.check(result,'recipients')['status'],'fail')

    def test_personalization_missing_values_and_fallback(self):
        self.person.city='';self.person.save()
        self.html('<p>{{city}}</p><a href="{{unsubscribe_url}}">Unsubscribe</a>')
        result=self.result();self.assertEqual(result['personalization']['missing'],{'city':1})
        self.assertEqual(self.check(result,'personalization')['status'],'fail')
        self.html('<p>{{city|вашем городе}}</p><a href="{{unsubscribe_url}}">Unsubscribe</a>')
        self.assertEqual(self.result()['personalization']['affected'],0)

    def test_multiple_clients_same_email_personalization_is_ambiguous(self):
        person=Client.objects.create(name='Beta',identity_key='beta',city='Астана')
        contact=Contact.objects.create(client=person,address=self.address);contact.audiences.add(self.audience)
        result=self.result()
        self.assertEqual(result['recipients']['total'],1)
        self.assertEqual(result['personalization']['ambiguous'],1)

    def test_company_is_not_invented_for_person(self):
        self.audience=Audience.objects.get_or_create(name_key='root:физические лица',defaults={'name':'Физические лица'})[0]
        self.contact.audiences.set([self.audience]);self.campaign.audiences.set([self.audience])
        self.html('<p>{{company}}</p><a href="{{unsubscribe_url}}">Unsubscribe</a>')
        self.assertEqual(self.result()['personalization']['missing'],{'company':1})

    def test_subject_header_injection_and_dynamic_link_after_substitution(self):
        self.person.name='javascript:alert(1)\nInjected';self.person.save()
        self.html('<p>Offer</p><a href="{{company}}">Shop</a><a href="{{unsubscribe_url}}">Unsubscribe</a>')
        result=self.result()
        self.assertEqual(result['personalization']['subject_errors'],1)
        self.assertEqual(result['personalization']['link_errors'],1)

    def test_static_template_errors_and_unsubscribe_placeholder(self):
        self.html('<script>alert(1)</script><p>{{unknown}}</p><a href="javascript:alert(1)">Bad</a>')
        result=self.result()
        self.assertEqual(self.check(result,'template')['status'],'fail')
        self.assertEqual(self.check(result,'links')['status'],'fail')
        self.assertTrue(any(row['code']=='unsubscribe' for row in result['template_findings']))

    def test_frozen_queue_uses_its_snapshot_and_remaining_recipients(self):
        self.confirm()
        queue=CampaignQueue.objects.create(campaign=self.campaign,snapshot={'template':self.campaign.template_snapshot,'sender':self.campaign.sender_snapshot,'campaign_revision':1})
        item=CampaignQueueItem.objects.create(queue=queue,address=self.address,email=self.address.email)
        self.html('<script>alert(1)</script>')
        result=self.result();self.assertEqual(result['recipients']['basis'],'queue')
        self.assertFalse(any(row['code']=='active' for row in result['template_findings']))
        item.state='simulated';item.save()
        result=self.result();self.assertEqual(result['recipients']['technical_ready'],0);self.assertEqual(result['recipients']['processed'],1)
        queue.state='cancelled';queue.save()
        self.assertEqual(self.check(self.result(),'queue_state')['status'],'fail')

    def test_empty_audiences_and_bad_schedule_are_reported(self):
        self.campaign.audiences.clear()
        self.campaign.schedule_mode='scheduled';self.campaign.scheduled_at=timezone.now()-timedelta(hours=1);self.campaign.save()
        result=self.result()
        self.assertEqual(self.check(result,'recipients')['status'],'fail')
        self.assertEqual(self.check(result,'schedule')['status'],'fail')

    def test_sender_fields_checked_and_no_secrets_returned(self):
        self.campaign.sender_snapshot={'name':'Bad\nHeader','email':'bad','reply_to':'bad'};self.campaign.save()
        result=self.result();self.assertEqual(self.check(result,'sender')['status'],'fail')
        self.assertNotIn('password',str(result).lower())
        self.assertNotIn('EMAIL_HOST_PASSWORD',str(result))

    @override_settings(SALES_SMTP_ENABLED=True,EMAIL_HOST='smtp.example.com',EMAIL_HOST_PASSWORD='not-for-report')
    def test_report_is_read_only_and_never_calls_transport_or_dns(self):
        self.confirm()
        before=(Address.objects.values().get(),OverShopSubscription.objects.values().get(),self.campaign.revision,CampaignTestEvent.objects.count())
        with patch('django.core.mail.EmailMultiAlternatives.send',side_effect=AssertionError('Mail used')),patch('smtplib.SMTP',side_effect=AssertionError('SMTP used')),patch('dns.resolver.Resolver.resolve',side_effect=AssertionError('DNS used')),patch('socket.create_connection',side_effect=AssertionError('Network used')):
            result=self.result()
        after=(Address.objects.values().get(),OverShopSubscription.objects.values().get(),EmailCampaign.objects.get().revision,CampaignTestEvent.objects.count())
        self.assertEqual(before,after);self.assertFalse(result['can_send']);self.assertNotIn('not-for-report',str(result))
        self.assertEqual(self.client.post(self.url,data={},content_type='application/json').status_code,405)
        self.assertEqual(self.client.get('/api/campaigns/999999/readiness').status_code,404)

    def test_centre_test_records_are_ignored_real_records_are_recognized(self):
        from .subscriptions import request_subscription,apply,unsubscribe_link
        test,raw,_=request_subscription({'email':self.address.email,'source':'Test form','is_test':True})
        apply(raw,True)
        self.assertEqual(self.result()['consent']['unknown'],1)
        real,raw,_=request_subscription({'email':self.address.email,'source':'Website form','is_test':False})
        apply(raw,True)
        self.assertEqual(self.result()['consent']['confirmed'],1)
        _,raw,_=unsubscribe_link(real.pk);apply(raw)
        result=self.result()
        self.assertEqual(result['consent']['revoked'],1)
        self.assertEqual(result['recipients']['technical_ready'],0)

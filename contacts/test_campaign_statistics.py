from django.test import override_settings as _legacy_auth
import uuid
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import EmailTemplate,CampaignSender,EmailCampaign,CampaignTestEvent,Address,CampaignQueue,CampaignQueueItem


@_legacy_auth(SALES_AUTH_REQUIRED=False)
class CampaignStatisticsTests(TestCase):
    def setUp(self):
        self.template=EmailTemplate.objects.create(name='Template')
        self.sender=CampaignSender.objects.create(name='Sender',email='sender@example.kz')
        self.campaign=EmailCampaign.objects.create(name='Campaign',template=self.template,sender=self.sender)
        self.other=EmailCampaign.objects.create(name='Other',template=self.template,sender=self.sender)
        self.default={'campaign_id':self.campaign.pk,'recipient':'person@example.com','kind':'sent'}

    def add(self,**data):
        payload={**self.default,'event_id':str(uuid.uuid4()),**data}
        return self.client.post('/api/campaign-stats/test-events',data=payload,content_type='application/json')
    def report(self,**params): return self.client.get('/api/campaign-stats',{'campaign_id':self.campaign.pk,**params})
    def demo(self,campaign='demo'): return self.client.post('/api/campaign-stats/demo',data={'campaign_id':campaign},content_type='application/json')

    def test_empty_statistics_no_implicit_events(self):
        result=self.report().json()
        self.assertEqual(result['unique']['delivered'],0)
        self.assertTrue(all(value is None for value in result['rates'].values()))
        self.assertEqual(result['history'],[])
        self.assertEqual(CampaignTestEvent.objects.count(),0)

    def test_repeated_activity_unique_metrics_and_totals(self):
        for kind in ('sent','delivered','open','open','click','click','unsubscribe','error','error'):
            self.assertEqual(self.add(kind=kind,**({'link':'https://example.com/offer'} if kind=='click' else {'error_type':'temporary'} if kind=='error' else {})).status_code,201)
        result=self.report().json()
        self.assertEqual(result['totals']['open'],2);self.assertEqual(result['unique']['open'],1)
        self.assertEqual(result['totals']['click'],2);self.assertEqual(result['unique']['click'],1)
        self.assertEqual(result['rates']['delivery'],100)
        self.assertEqual(result['rates']['open'],100)
        self.assertEqual(result['unique']['unsubscribe'],1)
        self.assertEqual(result['errors'][0]['total'],2)
        self.assertEqual(result['links'][0]['unique'],1)

    def test_event_id_idempotence_and_collision(self):
        event_id=str(uuid.uuid4())
        self.assertEqual(self.add(event_id=event_id).status_code,201)
        self.assertEqual(self.add(event_id=event_id,recipient='PERSON@EXAMPLE.COM').status_code,200)
        self.assertEqual(self.add(event_id=event_id,kind='open').status_code,400)
        self.assertEqual(CampaignTestEvent.objects.count(),1)

    def test_demo_counts_and_repeat_does_not_inflate(self):
        self.assertEqual(self.demo().status_code,200)
        before=CampaignTestEvent.objects.count()
        self.assertEqual(self.demo().json()['added'],0)
        self.assertEqual(CampaignTestEvent.objects.count(),before)
        result=self.report(campaign_id='demo').json()
        self.assertEqual(result['unique'],{'sent':100,'delivered':80,'open':50,'click':30,'unsubscribe':20,'error':20})
        self.assertEqual(result['totals']['open'],70);self.assertEqual(result['totals']['click'],40)
        self.assertEqual(result['rates'],{'delivery':80,'open':62.5,'click':37.5,'unsubscribe':25,'error':20})
        self.assertEqual(len(result['links']),2)
        self.assertEqual(len(result['days']),7)
        self.assertEqual(result['pagination']['total'],330)

    def test_demo_is_separate_from_campaigns_and_all_scope(self):
        self.add();self.demo()
        self.assertEqual(self.report().json()['totals']['sent'],1)
        self.assertEqual(self.report(campaign_id='all').json()['totals']['sent'],1)
        self.demo(self.other.pk)
        self.assertEqual(self.report(campaign_id=self.other.pk).json()['unique']['sent'],100)
        self.assertEqual(self.report().json()['unique']['sent'],1)

    def test_filters_log_only_and_pagination(self):
        self.demo(self.campaign.pk)
        result=self.report(kind='open',query='demo-',direction='asc',page=2).json()
        self.assertEqual(result['unique']['sent'],100)
        self.assertEqual(result['pagination']['page'],2)
        self.assertEqual(result['pagination']['total'],70)
        self.assertTrue(all(event['kind']=='open' for event in result['history']))
        self.assertEqual(len(result['history']),25)
        dates=[event['occurred_at'] for event in result['history']]
        self.assertEqual(dates,sorted(dates))

    def test_date_boundaries_in_server_timezone(self):
        self.add(occurred_at='2020-01-01T20:30:00+00:00')
        self.assertEqual(self.report(**{'to':'2020-01-01'}).json()['unique']['sent'],0)
        self.assertEqual(self.report(**{'from':'2020-01-02','to':'2020-01-02'}).json()['unique']['sent'],1)
        self.assertEqual(str(self.report().json()['days'][0]['day']),'2020-01-02')

    def test_missing_delivery_is_not_inferred_and_rate_uses_cohort(self):
        self.add(kind='open')
        result=self.report().json()
        self.assertEqual(result['unique']['delivered'],0)
        self.assertIsNone(result['rates']['open'])
        self.add(kind='delivered',recipient='other@example.com')
        self.assertEqual(self.report().json()['rates']['open'],0)

    def test_test_unsubscribe_and_errors_never_mutate_real_state(self):
        address=Address.objects.create(email='person@example.com',validation_result={'code':'mx'})
        queue=CampaignQueue.objects.create(campaign=self.campaign,state='paused')
        item=CampaignQueueItem.objects.create(queue=queue,address=address,email=address.email)
        self.add(kind='unsubscribe')
        self.add(kind='error',error_type='permanent')
        address.refresh_from_db();item.refresh_from_db();queue.refresh_from_db()
        self.assertFalse(address.archived);self.assertEqual(address.suppression_reason,'')
        self.assertEqual(item.state,'pending');self.assertEqual(queue.state,'paused')

    def test_invalid_event_inputs_and_unsafe_links(self):
        for data in [{'event_id':'bad'},{'kind':'unknown'},{'recipient':'bad'},{'source':'real'},
                     {'kind':'click','link':'javascript:alert(1)'},{'kind':'click'},
                     {'kind':'error','error_type':'bad'},{'kind':'sent','error_type':'permanent'},
                     {'campaign_id':True},{'campaign_id':999999},{'detail':'x'*501},
                     {'occurred_at':'invalid'},{'occurred_at':'1900-01-01T00:00:00Z'},
                     {'occurred_at':(timezone.now()+timedelta(days=1)).isoformat()}]:
            self.assertEqual(self.add(**data).status_code,400,data)
        self.assertEqual(CampaignTestEvent.objects.count(),0)

    def test_invalid_report_parameters(self):
        for params in [{'from':'2020-02-30'},{'from':'2020-02-02','to':'2020-01-01'},{'to':'9999-12-31'},
                       {'campaign_id':'bad'},{'kind':'unknown'},{'page':'bad'},{'page':'0'},{'direction':'bad'}]:
            self.assertEqual(self.report(**params).status_code,400)

    @override_settings(SALES_SMTP_ENABLED=True)
    def test_no_transport_for_demo_ingestion_or_report(self):
        with patch('django.core.mail.EmailMultiAlternatives.send',side_effect=AssertionError('Mail used')),patch('smtplib.SMTP',side_effect=AssertionError('SMTP used')),patch('socket.create_connection',side_effect=AssertionError('Network used')):
            self.demo();self.add();self.report()
        self.assertEqual(CampaignTestEvent.objects.count(),331)

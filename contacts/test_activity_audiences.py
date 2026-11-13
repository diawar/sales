from django.test import override_settings as _legacy_auth
import uuid
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase,override_settings
from django.utils import timezone
from .models import ActivityTestEvent,CampaignTestEvent,EmailTemplate,CampaignSender,EmailCampaign,Client,Address,Contact,Audience


@_legacy_auth(SALES_AUTH_REQUIRED=False)
class ActivityAudienceTests(TestCase):
    def setUp(self):self.now=timezone.now()
    def add(self,email,kind,days=0,message='',error='',**extra):
        return ActivityTestEvent.objects.create(event_id=uuid.uuid4(),recipient=email,kind=kind,occurred_at=self.now-timedelta(days=days),message_key=message,error_type=error,**extra)
    def deliveries(self,email,days):
        for i,day in enumerate(days):self.add(email,'delivered',day,str(i))
    def result(self,**params):
        response=self.client.get('/api/activity-audiences',{'as_of':self.now.isoformat(),**params})
        self.assertEqual(response.status_code,200,response.content)
        return response.json()
    def states(self,**params):return {r['email']:r for r in self.result(**params)['recipients']}

    def test_empty_report_is_read_only(self):
        result=self.result()
        self.assertEqual(result['total'],0);self.assertTrue(result['test_only']);self.assertFalse(result['can_send'])
        self.assertEqual(ActivityTestEvent.objects.count(),0)
        self.assertEqual(Audience.objects.filter(name__icontains='тест').count(),0)

    def test_click_is_active_open_is_not_and_passive_waits(self):
        self.add('active@example.com','click',1)
        self.deliveries('passive@example.com',[30,20,10]);self.add('passive@example.com','open',1)
        self.deliveries('waiting@example.com',[30,20,2])
        self.add('only-open@example.com','open',1)
        rows=self.states()
        self.assertEqual(rows['active@example.com']['state'],'active')
        self.assertEqual(rows['passive@example.com']['state'],'passive')
        self.assertEqual(rows['waiting@example.com']['state'],'insufficient')
        self.assertEqual(rows['only-open@example.com']['state'],'insufficient')

    def test_window_is_inclusive_and_future_events_ignored(self):
        self.add('boundary@example.com','click',90)
        self.add('old@example.com','click',90.00001)
        self.add('future@example.com','click',-1)
        rows=self.states()
        self.assertEqual(rows['boundary@example.com']['state'],'active')
        self.assertEqual(rows['old@example.com']['state'],'insufficient')
        self.assertNotIn('future@example.com',rows)

    def test_quiet_boundary_and_aging_without_profile_updates(self):
        self.deliveries('p@example.com',[30,20,7])
        self.assertEqual(self.states()['p@example.com']['state'],'passive')
        self.add('a@example.com','click',1)
        self.assertEqual(self.states()['a@example.com']['state'],'active')
        future=(self.now+timedelta(days=100)).isoformat()
        self.assertEqual(self.states(as_of=future)['a@example.com']['state'],'insufficient')

    def test_duplicate_delivery_does_not_make_three_letters(self):
        for day in (20,10,1):self.add('p@example.com','delivered',day,'one-message')
        row=self.states()['p@example.com']
        self.assertEqual(row['delivered'],1);self.assertEqual(row['state'],'insufficient')
        self.assertEqual(row['last_delivery'],(self.now-timedelta(days=20)).isoformat(timespec='milliseconds').replace('+00:00','Z'))

    def test_duplicate_does_not_reset_delivery_date_or_reenter_window(self):
        self.deliveries('p@example.com',[30,20,10]);self.add('p@example.com','delivered',1,'2')
        self.assertEqual(self.states()['p@example.com']['state'],'passive')
        self.add('old@example.com','delivered',100,'old-letter');self.add('old@example.com','delivered',1,'old-letter')
        self.assertEqual(self.states()['old@example.com']['delivered'],0)

    def test_optout_and_hard_error_are_sticky_and_dominate_clicks(self):
        self.add('out@example.com','unsubscribe',200);self.add('out@example.com','click',1)
        self.add('hard@example.com','error',200,error='permanent');self.add('hard@example.com','click',1)
        self.deliveries('soft@example.com',[30,20,10]);self.add('soft@example.com','error',1,error='temporary')
        rows=self.states()
        self.assertEqual(rows['out@example.com']['state'],'excluded')
        self.assertEqual(rows['hard@example.com']['state'],'excluded')
        self.assertEqual(rows['soft@example.com']['state'],'passive')

    def test_event_changes_group_automatically(self):
        self.deliveries('person@example.com',[30,20,10])
        self.assertEqual(self.states()['person@example.com']['state'],'passive')
        self.add('PERSON@EXAMPLE.COM','click',0)
        result=self.result();self.assertEqual(result['total'],1)
        self.assertEqual(result['recipients'][0]['state'],'active')

    def test_demo_is_separate_and_idempotent(self):
        self.client.post('/api/activity-audiences/demo',data={},content_type='application/json')
        count=ActivityTestEvent.objects.count()
        self.assertEqual(self.client.post('/api/activity-audiences/demo',data={},content_type='application/json').json()['added'],0)
        self.assertEqual(ActivityTestEvent.objects.count(),count)
        result=self.result(as_of=(timezone.now()+timedelta(seconds=1)).isoformat())
        self.assertEqual(result['total'],11)
        self.assertEqual(result['counts'],{'active':1,'passive':4,'insufficient':4,'excluded':2})
        self.assertEqual(CampaignTestEvent.objects.count(),0)

    def test_statistics_source_counts_distinct_campaign_messages(self):
        template=EmailTemplate.objects.create(name='T');sender=CampaignSender.objects.create(name='S',email='s@example.com')
        for i,days in enumerate((30,20,10)):
            campaign=EmailCampaign.objects.create(name='C'+str(i),template=template,sender=sender)
            for repeat in range(2):CampaignTestEvent.objects.create(event_id=uuid.uuid4(),campaign=campaign,recipient='p@example.com',kind='delivered',occurred_at=self.now-timedelta(days=days))
        CampaignTestEvent.objects.create(event_id=uuid.uuid4(),campaign=None,recipient='p@example.com',kind='click',occurred_at=self.now-timedelta(days=1))
        result=self.result(source='statistics',campaign_id='all')
        self.assertEqual(result['recipients'][0]['delivered'],3)
        self.assertEqual(result['recipients'][0]['state'],'passive')
        self.assertEqual(self.result(source='statistics',campaign_id='demo')['recipients'][0]['state'],'active')

    def test_rules_filters_and_sort_pagination(self):
        for i in range(30):self.add(f'p{i:02}@example.com','click',i)
        result=self.result(state='active',direction='desc',sort='email',page=2)
        self.assertEqual(result['pagination']['total'],30)
        self.assertEqual(len(result['recipients']),5)
        self.assertEqual(result['recipients'][0]['email'],'p04@example.com')
        result=self.result(query='p00')
        self.assertEqual(result['pagination']['total'],1);self.assertEqual(result['counts']['active'],30)
        self.deliveries('wait@example.com',[5,3,1])
        self.assertEqual(self.states(quiet_days=0,query='wait@')['wait@example.com']['state'],'passive')

    def test_invalid_parameters(self):
        for params in [{'source':'real'},{'lookback_days':0},{'min_delivered':0},{'quiet_days':91},{'lookback_days':'bad'},
                       {'as_of':'bad'},{'state':'bad'},{'sort':'bad'},{'direction':'bad'},{'page':0}]:
            self.assertEqual(self.client.get('/api/activity-audiences',params).status_code,400)
        self.assertEqual(self.client.post('/api/activity-audiences',data={'action':'apply_to_clients'},content_type='application/json').status_code,405)

    def test_event_api_validates_deduplicates_and_detects_conflicts(self):
        payload={'event_id':str(uuid.uuid4()),'recipient':'person@example.com','kind':'delivered','message_key':'letter-1'}
        self.assertEqual(self.client.post('/api/activity-audiences/test-events',data=payload,content_type='application/json').status_code,201)
        self.assertEqual(self.client.post('/api/activity-audiences/test-events',data=payload,content_type='application/json').status_code,200)
        self.assertEqual(self.client.post('/api/activity-audiences/test-events',data={**payload,'recipient':'other@example.com'},content_type='application/json').status_code,400)
        for fields in [{'recipient':'bad'},{'message_key':''},{'source':'real'},{'test_only':False},{'occurred_at':'bad'},{'kind':'unknown'}]:
            self.assertEqual(self.client.post('/api/activity-audiences/test-events',data={**payload,'event_id':str(uuid.uuid4()),**fields},content_type='application/json').status_code,400)
        self.assertEqual(ActivityTestEvent.objects.count(),1)

    @override_settings(SALES_SMTP_ENABLED=True)
    def test_real_base_and_transport_are_untouched(self):
        person=Client.objects.create(name='Real',identity_key='real')
        address=Address.objects.create(email='activity-active@example.invalid',suppression_reason='Жалоба')
        contact=Contact.objects.create(client=person,address=address)
        before=(Client.objects.values().get(),Address.objects.values().get(),Contact.objects.values().get(),list(contact.audiences.values_list('pk',flat=True)))
        with patch('django.core.mail.EmailMultiAlternatives.send',side_effect=AssertionError('Mail used')),patch('smtplib.SMTP',side_effect=AssertionError('SMTP used')),patch('socket.create_connection',side_effect=AssertionError('Network used')):
            self.client.post('/api/activity-audiences/demo',data={},content_type='application/json');self.result()
        after=(Client.objects.values().get(),Address.objects.values().get(),Contact.objects.values().get(),list(contact.audiences.values_list('pk',flat=True)))
        self.assertEqual(before,after)

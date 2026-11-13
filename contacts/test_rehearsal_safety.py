from django.test import override_settings as _legacy_auth
import uuid
from unittest.mock import patch
from django.test import TestCase, override_settings, Client
from . import test_rehearsal_delivery as fixtures
from . import rehearsal, rehearsal_safety as safety
from .models import (RehearsalDeliveryEvent, Address, EmailExclusion, EmailSubscription,
    ActivityTestEvent, CampaignTestEvent, CampaignQueue, AutomationSimulationAction,
    TrackedMessage, CampaignSender, EmailCampaign)


@override_settings(SALES_PUBLIC_BASE_URL='https://sales.example.com')
@_legacy_auth(SALES_AUTH_REQUIRED=False)
class RehearsalSafetyTests(TestCase):
    setUp=fixtures.RehearsalDeliveryTests.setUp
    start=fixtures.RehearsalDeliveryTests.start
    event=fixtures.RehearsalDeliveryTests.event
    act=fixtures.RehearsalDeliveryTests.act
    result=fixtures.RehearsalDeliveryTests.result

    def configure(self,row,**changes):
        config={**safety.DEFAULT,'min_sample':1,'errors_percent':'100','permanent_percent':'100','complaints_percent':'100',**changes}
        return self.act(row,'safety_configure',policy_revision=row.snapshot.get('safety_revision',0),policy=config)

    def add_people(self,count=4):
        for i in range(count-1):self.event('active-'+str(i),'click',1)

    def test_error_threshold_stops_all_preparation_and_retries(self):
        self.add_people();row=self.configure(self.start(),errors_percent='20')
        letter=row.letters.filter(recipient='active@example.invalid').get()
        row=self.result(row,letter=letter)
        self.assertEqual(row.state,'safety_stopped');self.assertEqual(row.letters.count(),4)
        letter.refresh_from_db();self.assertEqual(letter.delivery_state,'stopped');self.assertIsNone(letter.retry_at)
        self.assertTrue(all(m['state']=='safety_stopped' for m in row.members))
        stop=row.snapshot['safety_stop'];self.assertIn('1/1',stop['reason']);self.assertIn('20',stop['reason'])
        self.assertEqual(stop['breaches'][0]['metric'],'errors');self.assertEqual(stop['policy_revision'],2)
        self.act(row,'tick');self.assertEqual(row.letters.count(),4)
        with self.assertRaises(ValueError):self.act(row,'resume')
        with self.assertRaises(ValueError):self.act(row,'advance',minutes=15)
        self.assertEqual(row.logs.filter(kind='safety_stopped').count(),1)
        row.state='running';row.save(update_fields=['state'])
        row=self.act(row,'tick');self.assertEqual(row.state,'safety_stopped');self.assertEqual(row.letters.count(),4)

    def test_minimum_sample_and_strict_boundary(self):
        self.add_people(3);row=self.configure(self.start(),min_sample=2,errors_percent='50')
        letters=list(row.letters.order_by('pk'));row=self.result(row,letter=letters[0])
        self.assertEqual(row.state,'running')
        row=self.result(row,'test_delivered',letter=letters[1]);self.assertEqual(row.state,'running')
        metric=safety.assessment(row)['campaigns'][0]['metrics']['errors']
        self.assertEqual(metric['percent'],'50.0000');self.assertFalse(metric['exceeded'])
        row=self.result(row,letter=letters[2]);self.assertEqual(row.state,'safety_stopped')
        self.assertEqual(row.snapshot['safety_stop']['breaches'][0]['sample'],3)

    def test_permanent_threshold_independent_from_all_errors(self):
        row=self.configure(self.start(),permanent_percent='0')
        row=self.result(row,'permanent_error')
        self.assertEqual(row.state,'safety_stopped')
        self.assertEqual([b['metric'] for b in row.snapshot['safety_stop']['breaches']],['permanent'])

    def test_complaint_threshold_after_test_delivery(self):
        row=self.configure(self.start(),complaints_percent='0')
        row=self.result(row,'test_delivered');self.assertEqual(row.state,'running')
        row=self.result(row,'complaint');self.assertEqual(row.state,'safety_stopped')
        self.assertEqual(row.snapshot['safety_stop']['breaches'][0]['metric'],'complaints')
        self.assertEqual(row.snapshot['safety_stop']['breaches'][0]['sample'],1)

    def test_sample_is_known_messages_not_prepared_or_attempts(self):
        self.add_people(5);row=self.configure(self.start(),min_sample=2,errors_percent='0')
        letter=row.letters.first();row=self.result(row,letter=letter)
        row=self.act(row,'advance',minutes=15);row=self.result(row,letter=letter)
        stats=safety.assessment(row);self.assertEqual(stats['campaigns'][0]['sample'],1)
        self.assertEqual(stats['campaigns'][0]['metrics']['errors']['count'],1)
        self.assertEqual(row.state,'running');self.assertEqual(row.letters.count(),5)

    def test_exact_decimal_comparison_without_rounding_bypass(self):
        self.add_people(3);row=self.configure(self.start(),min_sample=3,errors_percent='33.33')
        letters=list(row.letters.order_by('pk'))
        row=self.result(row,'test_delivered',letter=letters[0]);row=self.result(row,'test_delivered',letter=letters[1])
        row=self.result(row,letter=letters[2]);self.assertEqual(row.state,'safety_stopped')
        self.assertEqual(row.snapshot['safety_stop']['breaches'][0]['percent'],'33.3333')

    def test_results_of_other_rehearsals_and_sources_do_not_count(self):
        row=self.configure(self.start(),complaints_percent='0');other=self.start()
        self.result(other,'complaint');row=self.act(row,'tick')
        self.assertEqual(row.state,'running');self.assertEqual(safety.assessment(row)['campaigns'],[])
        self.assertFalse(CampaignTestEvent.objects.exists())

    def test_each_campaign_uses_its_own_denominator(self):
        row=self.start();sender=CampaignSender.objects.create(name='Other',email='other@example.invalid')
        c=EmailCampaign.objects.create(name='Second',template=self.template,sender=sender,
            template_snapshot=self.campaign.template_snapshot,sender_snapshot={'name':sender.name,'email':sender.email,'reply_to':''})
        row=self.configure(row,errors_percent='100')
        row=self.result(row,'test_delivered');row=self.act(row,'advance',minutes=60)
        second=row.letters.order_by('pk').last();second.message.campaign=c;second.message.save(update_fields=['campaign'])
        row.snapshot['steps'][1]['campaign_id']=c.pk;row.snapshot['steps'][1]['campaign_name']=c.name;row.save(update_fields=['snapshot'])
        row=self.configure(row,errors_percent='50');row=self.result(row,letter=second)
        self.assertEqual(row.state,'safety_stopped')
        breach=row.snapshot['safety_stop']['breaches'][0]
        self.assertEqual((breach['campaign_id'],breach['sample'],breach['count']),(c.pk,1,1))

    def test_paused_configuration_evaluates_existing_results(self):
        row=self.start();row=self.result(row);row=self.act(row,'pause')
        row=self.configure(row,errors_percent='0')
        self.assertEqual(row.state,'safety_stopped');self.assertEqual(row.letters.get().delivery_state,'stopped')
        with self.assertRaises(ValueError):self.configure(row,enabled=False)

    def test_stop_is_sticky_reason_snapshot_immutable_and_cancel_allowed(self):
        self.add_people(3);row=self.configure(self.start(),errors_percent='50')
        letters=list(row.letters.order_by('pk'));eid=uuid.uuid4()
        row=self.result(row,letter=letters[0],event_id=eid);stop=row.snapshot['safety_stop']
        row=self.result(row,letter=letters[0],event_id=eid)
        for letter in letters[1:]:row=self.result(row,'test_delivered',letter=letter)
        self.assertEqual(row.state,'safety_stopped');self.assertEqual(row.snapshot['safety_stop'],stop)
        self.assertFalse(safety.assessment(row)['breaches']);self.assertEqual(row.logs.filter(kind='safety_stopped').count(),1)
        row=self.act(row,'cancel');self.assertEqual(row.state,'cancelled');self.assertEqual(row.snapshot['safety_stop'],stop)
        self.assertFalse(any(m['state']=='safety_stopped' for m in row.members))

    def test_disabled_guard_and_legacy_do_not_retroactively_stop(self):
        row=self.configure(self.start(),enabled=False,errors_percent='0');row=self.result(row)
        self.assertEqual(row.state,'running');self.assertFalse(row.snapshot.get('safety_stop'))
        row.snapshot.pop('safety_policy');row.snapshot.pop('safety_revision');row.save(update_fields=['snapshot'])
        row=self.act(row,'tick');self.assertEqual(row.state,'running');self.assertFalse(safety.assessment(row)['policy']['enabled'])

    def test_validation_version_and_api_get_no_side_effects(self):
        for data in ({'min_sample':True},{'min_sample':0},{'errors_percent':'NaN'},{'errors_percent':'0.001'},
            {'complaints_percent':101},{'enabled':'yes'},{'permanent_percent':-1},{'unknown':1}):
            with self.assertRaises(ValueError):safety.policy(data)
        row=self.start();revision=row.snapshot['safety_revision'];row=self.configure(row)
        with self.assertRaises(ValueError):self.act(row,'safety_configure',policy_revision=revision,policy=safety.DEFAULT)
        self.assertEqual(Client(enforce_csrf_checks=True).post(f'/api/rehearsals/{row.pk}',{},content_type='application/json').status_code,403)
        before=row.logs.count();data=self.client.get(f'/api/rehearsals/{row.pk}').json()
        self.assertEqual(row.logs.count(),before);self.assertFalse(data['safety']['can_send'])
        self.assertIn('policy',data['safety']);self.assertFalse(data['safety']['stop'])

    def test_default_on_new_session_and_no_production_mutations_or_network(self):
        Address.objects.create(email='active@example.invalid',validation_result={'code':'mx'})
        EmailSubscription.objects.create(email='active@example.invalid',is_test=False,state='confirmed')
        before=(list(Address.objects.values()),list(EmailSubscription.objects.filter(is_test=False).values()),list(ActivityTestEvent.objects.values()))
        with patch('smtplib.SMTP',side_effect=AssertionError('SMTP')),patch('socket.create_connection',side_effect=AssertionError('network')),patch('httpx.Client.send',side_effect=AssertionError('HTTP')),patch('django.core.mail.send_mail',side_effect=AssertionError('mail')):
            row=self.start();self.assertTrue(row.snapshot['safety_policy']['enabled'])
            row=self.configure(row,complaints_percent='0');row=self.result(row,'complaint')
        self.assertEqual(before,(list(Address.objects.values()),list(EmailSubscription.objects.filter(is_test=False).values()),list(ActivityTestEvent.objects.values())))
        self.assertFalse(EmailExclusion.objects.exists());self.assertFalse(CampaignQueue.objects.exists());self.assertFalse(AutomationSimulationAction.objects.exists())
        self.assertEqual(TrackedMessage.objects.count(),1)

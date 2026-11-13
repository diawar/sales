from django.test import override_settings as _legacy_auth
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, Client, override_settings
from django.utils import timezone
from . import test_rehearsal_delivery as fixtures
from . import test_frequency as frequency, rehearsal, automation_simulation as engine
from .models import (TestFrequencyPolicy, TestFrequencyChange, AutomationSimulationAction, AutomationSimulationClock,
    CampaignSender, EmailCampaign, Address, EmailExclusion, EmailSubscription, ActivityTestEvent,
    RehearsalDeliveryEvent, TrackedMessage)


@override_settings(SALES_PUBLIC_BASE_URL='https://sales.example.com')
@_legacy_auth(SALES_AUTH_REQUIRED=False)
class SharedFrequencyTests(TestCase):
    event=fixtures.RehearsalDeliveryTests.event
    start=fixtures.RehearsalDeliveryTests.start
    act=fixtures.RehearsalDeliveryTests.act
    result=fixtures.RehearsalDeliveryTests.result

    def setUp(self):
        fixtures.RehearsalDeliveryTests.setUp(self)
        self.base.update(steps=[{'campaign_id':self.campaign.pk,'delay_minutes':0}],
            min_interval_minutes=1,max_24h=10,max_7d=30)
        self.configure(min_interval_minutes=10,max_24h=10,max_7d=20)

    def configure(self,**changes):
        gate=TestFrequencyPolicy.objects.get(pk=1)
        policy={k:getattr(gate,k) for k in frequency.FIELDS};policy.update(enabled=True);policy.update(changes)
        return frequency.configure({'mode':'simulation','test_only':True,'revision':gate.revision,'policy':policy})

    def scenario(self,**changes):
        row=engine.save({'mode':'simulation','test_only':True,'name':'Shared limit','config':self.base|changes})
        return engine.start(row.pk,row.revision)

    def test_between_rehearsals_defers_at_shared_clock_and_exact_interval(self):
        a=self.start();b=self.start()
        self.assertEqual(a.letters.count(),1);self.assertEqual(b.letters.count(),0)
        self.assertEqual(b.members[0]['next_at'],(a.clock_at+timedelta(minutes=10)).isoformat())
        self.assertIn('репетиция',b.members[0]['detail']);self.assertIn('рассылка',b.members[0]['detail'])
        self.act(b,'tick');self.assertEqual(b.logs.filter(kind='global_frequency').count(),1)
        b=self.act(b,'advance',minutes=9);self.assertEqual(b.letters.count(),0)
        b=self.act(b,'advance',minutes=1);self.assertEqual(b.letters.count(),1)
        self.assertEqual(frequency.plan('active@example.invalid',b.clock_at)['count_24h'],2)

    def test_scenario_action_defers_rehearsal(self):
        run=self.scenario();self.assertEqual(run.members.get().state,'completed')
        row=self.start();self.assertEqual(row.letters.count(),0);self.assertIn('сценарий',row.members[0]['detail'])
        row=self.act(row,'advance',minutes=10);self.assertEqual(row.letters.count(),1)
        self.assertEqual(AutomationSimulationAction.objects.count(),1)

    def test_rehearsal_defers_scenario_and_synchronizes_clocks(self):
        row=self.start();run=self.scenario();member=run.members.get()
        self.assertEqual(member.state,'waiting');self.assertIn('репетиция',member.detail)
        self.assertEqual(engine.tick(9),0);self.assertEqual(engine.tick(1),1)
        member.refresh_from_db();self.assertEqual(member.state,'completed')
        row=self.act(row,'tick');self.assertEqual(row.clock_at,AutomationSimulationClock.objects.get().clock_at)

    def test_distinct_campaigns_share_one_limit(self):
        self.configure(min_interval_minutes=1,max_24h=1,max_7d=10)
        sender=CampaignSender.objects.create(name='Other',email='other@example.invalid')
        c=EmailCampaign.objects.create(name='Other campaign',template=self.template,sender=sender,
            template_snapshot=self.campaign.template_snapshot,sender_snapshot={'name':sender.name,'email':sender.email,'reply_to':''})
        a=self.start();b=self.start(steps=[{'campaign_id':c.pk,'delay_minutes':0}])
        self.assertEqual(b.letters.count(),0);self.assertIn('24 часа',b.members[0]['detail'])
        b=self.act(b,'advance',minutes=1440);self.assertEqual(b.letters.get().message.campaign_id,c.pk)
        self.assertEqual({r['campaign_id'] for r in frequency.history()}, {self.campaign.pk,c.pk})

    def test_sliding_daily_weekly_boundaries_and_no_reset_at_midnight(self):
        self.configure(min_interval_minutes=1,max_24h=1,max_7d=2)
        first=self.start();second=self.start()
        second=self.act(second,'advance',minutes=1439);self.assertEqual(second.letters.count(),0)
        second=self.act(second,'advance',minutes=1);self.assertEqual(second.letters.count(),1)
        third=self.start();self.assertEqual(third.letters.count(),0)
        self.assertEqual(third.members[0]['next_at'],(first.clock_at+timedelta(days=7)).isoformat())
        third=self.act(third,'advance',minutes=8639);self.assertEqual(third.letters.count(),0)
        third=self.act(third,'advance',minutes=1);self.assertEqual(third.letters.count(),1)

    def test_normalized_email_and_distinct_people(self):
        row=self.start();decision=frequency.plan('  ACTIVE@EXAMPLE.INVALID ',row.clock_at)
        self.assertEqual(decision['count_24h'],1);self.assertGreater(decision['allowed_at'],row.clock_at)
        other=frequency.plan('different@example.invalid',row.clock_at)
        self.assertEqual(other['allowed_at'],row.clock_at);self.assertEqual(other['count_7d'],0)

    def test_retries_and_outcomes_do_not_create_extra_contacts(self):
        row=self.start();row=self.result(row);row=self.act(row,'advance',minutes=15)
        self.assertEqual(row.letters.get().attempt_count,2)
        row=self.result(row,'test_delivered')
        self.assertEqual(len(frequency.history()),1);self.assertEqual(TrackedMessage.objects.count(),1)
        self.assertEqual(RehearsalDeliveryEvent.objects.count(),2)

    def test_failed_preparation_does_not_consume_limit(self):
        bad=self.start(values={});self.assertEqual(bad.letters.count(),0)
        good=self.start();self.assertEqual(good.letters.count(),1);self.assertEqual(len(frequency.history()),1)

    def test_cancellation_and_safety_stop_do_not_refund_history(self):
        self.configure(min_interval_minutes=1,max_24h=1,max_7d=10)
        row=self.start();row=self.act(row,'safety_configure',policy_revision=1,
            policy={'enabled':True,'min_sample':1,'errors_percent':'0','permanent_percent':'100','complaints_percent':'100'})
        row=self.result(row);self.assertEqual(row.state,'safety_stopped')
        self.act(row,'cancel');other=self.start();self.assertEqual(other.letters.count(),0)
        self.assertEqual(len(frequency.history()),1)

    def test_policy_change_rechecks_wait_without_losing_next_step_delay(self):
        self.configure(min_interval_minutes=60)
        self.start();row=self.start(steps=[{'campaign_id':self.campaign.pk,'delay_minutes':0},
            {'campaign_id':self.campaign.pk,'delay_minutes':120}])
        self.assertEqual(row.letters.count(),0)
        self.configure(enabled=False);row=self.act(row,'tick');self.assertEqual(row.letters.count(),1)
        self.configure(enabled=False);row=self.act(row,'tick');self.assertEqual(row.letters.count(),1)
        row=self.act(row,'advance',minutes=120);self.assertEqual(row.letters.count(),2)

    def test_scenario_policy_change_preserves_wait_and_local_limits(self):
        self.configure(min_interval_minutes=60);self.start()
        run=self.scenario();self.assertEqual(run.members.get().state,'waiting')
        self.configure(min_interval_minutes=1);self.assertEqual(engine.tick(0),0)
        self.assertEqual(engine.tick(1),1);self.assertEqual(run.members.get().state,'completed')

    def test_local_limits_can_be_stricter_than_global(self):
        self.configure(min_interval_minutes=1,max_24h=100,max_7d=500)
        row=self.start(steps=[{'campaign_id':self.campaign.pk,'delay_minutes':0},{'campaign_id':self.campaign.pk,'delay_minutes':1}],max_24h=1)
        row=self.act(row,'advance',minutes=1);self.assertEqual(row.letters.count(),1)
        row=self.act(row,'advance',minutes=1439);self.assertEqual(row.letters.count(),2)

    def test_explanation_uses_stricter_combined_due_time(self):
        self.start();gate=TestFrequencyPolicy.objects.get()
        d=frequency.plan('active@example.invalid',gate.clock_at)
        later=gate.clock_at+timedelta(days=1)
        detail=frequency.explanation(d,later)
        self.assertIn('местные ограничения',detail)
        self.assertIn(timezone.localtime(later).isoformat(),detail)

    def test_enabling_aligns_future_test_history_without_rewinding_time(self):
        self.configure(enabled=False)
        row=self.start(steps=[{'campaign_id':self.campaign.pk,'delay_minutes':60}])
        row=self.act(row,'advance',minutes=60);self.assertEqual(row.letters.count(),1)
        self.assertEqual(frequency.report({})['total'],1)
        gate=self.configure(enabled=True)
        self.assertGreaterEqual(gate.clock_at,row.clock_at)
        other=self.start();self.assertEqual(other.letters.count(),0)

    def test_api_filters_audit_validation_and_read_only_get(self):
        self.start();self.scenario()
        before=TestFrequencyPolicy.objects.get().revision
        data=self.client.get('/api/test-frequency?query=ACTIVE%40example.invalid&source=rehearsal').json()
        self.assertFalse(data['can_send']);self.assertEqual(data['pagination']['total'],1)
        self.assertEqual(data['decision']['count_24h'],1);self.assertEqual(TestFrequencyPolicy.objects.get().revision,before)
        self.assertEqual(data['changes'][0]['revision'],before)
        self.assertEqual(self.client.get('/api/test-frequency?source=bad').status_code,400)
        self.assertEqual(self.client.get('/api/test-frequency?page=no').status_code,400)
        policy={**frequency.DEFAULT,'min_interval_minutes':1,'max_24h':10,'max_7d':20}
        payload={'mode':'simulation','test_only':True,'revision':before,'policy':policy}
        self.assertEqual(Client(enforce_csrf_checks=True).post('/api/test-frequency',payload,content_type='application/json').status_code,403)
        for bad in ({'send':True},{'test_only':False},{'revision':before-1},{'policy':policy|{'max_24h':True}},
            {'policy':policy|{'max_7d':1}},{'policy':policy|{'min_interval_minutes':0}}):
            self.assertEqual(self.client.post('/api/test-frequency',payload|bad,content_type='application/json').status_code,400)

    def test_no_network_and_no_production_contact_changes(self):
        Address.objects.create(email='active@example.invalid',validation_result={'code':'mx'})
        EmailSubscription.objects.create(email='active@example.invalid',is_test=False,state='confirmed')
        before=(list(Address.objects.values()),list(EmailSubscription.objects.filter(is_test=False).values()),list(ActivityTestEvent.objects.values()))
        with patch('socket.create_connection',side_effect=AssertionError('network')),patch('smtplib.SMTP',side_effect=AssertionError('SMTP')),patch('httpx.Client.send',side_effect=AssertionError('HTTP')),patch('django.core.mail.send_mail',side_effect=AssertionError('mail')):
            self.start();run=self.scenario();self.assertEqual(run.members.get().state,'waiting')
            self.act(self.start(),'advance',minutes=10)
        self.assertEqual(before,(list(Address.objects.values()),list(EmailSubscription.objects.filter(is_test=False).values()),list(ActivityTestEvent.objects.values())))
        self.assertFalse(EmailExclusion.objects.exists())

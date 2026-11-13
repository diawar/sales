from django.test import override_settings as _legacy_auth
import io
import uuid
from datetime import timedelta
from unittest.mock import patch
from django.core.management import call_command
from django.test import TestCase,Client as HttpClient
from django.utils import timezone
from . import automation_simulation as engine
from .models import (EmailTemplate,CampaignSender,EmailCampaign,ActivityTestEvent,AutomationScenario,
    AutomationSimulationClock,AutomationSimulationRun,AutomationSimulationMember,AutomationSimulationAction,
    Address,EmailExclusion,EmailSubscription,TrackedMessage,CampaignTestEvent,CampaignQueue,TrackingEvent)


@_legacy_auth(SALES_AUTH_REQUIRED=False)
class AutomationSimulationTests(TestCase):
    def setUp(self):
        self.now=timezone.now().replace(microsecond=0)
        from .models import TestFrequencyPolicy
        TestFrequencyPolicy.objects.create(pk=1,enabled=False,clock_at=self.now)
        AutomationSimulationClock.objects.create(pk=1,clock_at=self.now)
        template=EmailTemplate.objects.create(name='Mail',subject='Saved subject',preheader='News',html='<p>Offer</p><a href="{{unsubscribe_url}}">Unsubscribe</a>')
        sender=CampaignSender.objects.create(name='OVER',email='sender@example.invalid')
        self.campaign=EmailCampaign.objects.create(name='Step campaign',template=template,sender=sender,
            template_snapshot={k:getattr(template,k) for k in ('id','name','subject','preheader','html','revision')},
            sender_snapshot={k:getattr(sender,k) for k in ('name','email','reply_to')})
        for person in ('active','passive','reader'):
            for i,day in enumerate((30,20,10)):self.event(person,'delivered',days=day,message=str(i))
        self.event('active','click',days=1);self.event('reader','open',days=1)
        self.event('optout','click',days=1);self.event('optout','unsubscribe',days=0)
        self.event('error','error',days=0,error='permanent')
        self.base={'target':'active','steps':[{'campaign_id':self.campaign.pk,'delay_minutes':10},
            {'campaign_id':self.campaign.pk,'delay_minutes':60}],'min_interval_minutes':30,'max_24h':2,'max_7d':3,'cooldown_days':7}

    def event(self,person,kind,days=0,message='',error='',at=None):
        return ActivityTestEvent.objects.create(event_id=uuid.uuid4(),recipient=person+'@example.invalid',kind=kind,
            message_key=message,error_type=error,occurred_at=at or self.now-timedelta(days=days))

    def save(self,**config):
        return engine.save({'mode':'simulation','test_only':True,'name':'Scenario','config':self.base|config})

    def start(self,**config):
        row=self.save(**config);return engine.start(row.pk,row.revision)

    def post(self,data):return self.client.post('/api/automation-simulation',{'mode':'simulation','test_only':True,**data},content_type='application/json')

    def test_conditions_delays_and_one_pass_per_recipient(self):
        run=self.start();m=run.members.get()
        self.assertEqual(m.recipient,'active@example.invalid');self.assertEqual(m.next_at,self.now+timedelta(minutes=10))
        self.assertEqual(engine.tick(9),0);self.assertEqual(engine.tick(1),1)
        self.assertEqual(engine.tick(59),0);self.assertEqual(engine.tick(1),1)
        m.refresh_from_db();self.assertEqual(m.state,'completed');self.assertEqual(m.step_index,2)
        self.assertEqual(engine.tick(0),0);self.assertEqual(run.members.count(),1)

    def test_passive_and_open_conditions_use_existing_classification(self):
        run=self.start(target='passive',opens='none')
        self.assertEqual(list(run.members.values_list('recipient',flat=True)),['passive@example.invalid'])
        run2=self.start(target='passive',opens='some')
        self.assertEqual(list(run2.members.values_list('recipient',flat=True)),['reader@example.invalid'])
        self.assertFalse(run.members.filter(recipient='optout@example.invalid').exists())
        self.assertTrue(run.logs.filter(recipient='optout@example.invalid',kind='not_eligible').exists())

    def test_change_of_group_or_optout_stops_future_steps(self):
        run=self.start(target='passive')
        self.event('passive','click',at=self.now+timedelta(minutes=1))
        self.event('reader','unsubscribe',at=self.now+timedelta(minutes=1))
        engine.tick(2)
        states=dict(run.members.values_list('recipient','state'))
        self.assertEqual(states,{'passive@example.invalid':'exited','reader@example.invalid':'excluded'})
        self.assertEqual(engine.tick(60),0);self.assertEqual(AutomationSimulationAction.objects.count(),0)

    def test_rules_rechecked_before_delayed_step_and_clock_jump_does_not_replay(self):
        run=self.start(lookback_days=1,quiet_days=0,steps=[{'campaign_id':self.campaign.pk,'delay_minutes':2880}])
        engine.tick(1440);self.assertEqual(run.members.get().state,'exited')
        self.assertEqual(engine.tick(1440),0)

    def test_pause_resume_cancel_and_idempotent_tick(self):
        run=self.start();engine.control(run.pk,'pause');self.assertEqual(engine.tick(20),0)
        engine.control(run.pk,'resume');self.assertEqual(AutomationSimulationAction.objects.count(),1)
        engine.control(run.pk,'resume');engine.tick(0);self.assertEqual(AutomationSimulationAction.objects.count(),1)
        engine.control(run.pk,'cancel');engine.tick(1440)
        self.assertEqual(run.members.get().state,'cancelled');self.assertEqual(AutomationSimulationAction.objects.count(),1)
        with self.assertRaises(ValueError):engine.control(run.pk,'resume')

    def test_frequency_24h_and_exact_boundary(self):
        run=self.start(steps=[{'campaign_id':self.campaign.pk,'delay_minutes':0}]*2,min_interval_minutes=1,max_24h=1)
        self.assertEqual(AutomationSimulationAction.objects.count(),1)
        self.assertEqual(engine.tick(1),0);m=run.members.get();self.assertEqual(m.next_at,self.now+timedelta(days=1))
        self.assertEqual(engine.tick(1438),0);self.assertEqual(engine.tick(1),1)
        self.assertTrue(run.logs.filter(kind='frequency').exists())

    def test_frequency_7d_and_cross_scenario_history(self):
        first=self.start(steps=[{'campaign_id':self.campaign.pk,'delay_minutes':0}],min_interval_minutes=1,max_24h=1,max_7d=1)
        second=self.start(steps=[{'campaign_id':self.campaign.pk,'delay_minutes':0}],min_interval_minutes=1,max_24h=1,max_7d=1)
        self.assertEqual(AutomationSimulationAction.objects.count(),1)
        self.assertEqual(second.members.get().next_at,self.now+timedelta(days=7))
        self.assertEqual(engine.tick(6*1440),0);self.assertEqual(engine.tick(1440),1)
        self.assertEqual(first.members.get().state,'completed');self.assertEqual(second.members.get().state,'completed')

    def test_minimum_interval_applies_across_scenarios(self):
        self.start(steps=[{'campaign_id':self.campaign.pk,'delay_minutes':0}],min_interval_minutes=60,max_24h=10,max_7d=10)
        second=self.start(steps=[{'campaign_id':self.campaign.pk,'delay_minutes':0}],min_interval_minutes=60,max_24h=10,max_7d=10)
        self.assertEqual(second.members.get().next_at,self.now+timedelta(minutes=60))
        self.assertEqual(engine.tick(59),0);self.assertEqual(engine.tick(1),1)

    def test_cooldown_and_no_duplicate_open_run(self):
        run=self.start(steps=[{'campaign_id':self.campaign.pk,'delay_minutes':0}])
        with self.assertRaises(ValueError):engine.start(run.scenario_id,1)
        engine.control(run.pk,'cancel');again=engine.start(run.scenario_id,1)
        self.assertEqual(again.members.count(),0);self.assertTrue(again.logs.filter(kind='cooldown').exists())
        engine.tick(7*1440);self.assertEqual(again.members.count(),1)

    def test_run_freezes_steps_and_rules_when_definition_or_campaign_changes(self):
        row=self.save();run=engine.start(row.pk,1)
        self.campaign.template_snapshot['subject']='Changed';self.campaign.save()
        engine.save({'mode':'simulation','test_only':True,'revision':1,'name':'New name','config':self.base|{'target':'passive'}},row.pk)
        engine.tick(10);action=AutomationSimulationAction.objects.get()
        self.assertEqual(action.snapshot['template']['subject'],'Saved subject')
        self.assertEqual(action.member.recipient,'active@example.invalid')
        run.refresh_from_db();self.assertEqual(run.snapshot['revision'],1)

    def test_no_real_data_or_sending_even_for_matching_real_address(self):
        address=Address.objects.create(email='active@example.invalid',suppression_reason='Real complaint')
        subscription=EmailSubscription.objects.create(email=address.email,is_test=False,state='confirmed',source='Source',consent_text='Yes',consent_version='1')
        counts={model:model.objects.count() for model in (Address,EmailExclusion,EmailSubscription,TrackedMessage,CampaignTestEvent,CampaignQueue,TrackingEvent)}
        with patch('django.core.mail.message.EmailMessage.send') as mail,patch('smtplib.SMTP') as smtp,patch('httpx.Client.request') as network:
            self.start();engine.tick(120);call_command('process_automation_simulations',stdout=io.StringIO())
        mail.assert_not_called();smtp.assert_not_called();network.assert_not_called()
        for model,count in counts.items():self.assertEqual(model.objects.count(),count)
        address.refresh_from_db();subscription.refresh_from_db()
        self.assertEqual(address.suppression_reason,'Real complaint');self.assertEqual(subscription.state,'confirmed')

    def test_invalid_definitions_modes_versions_and_clock_steps(self):
        for config in ({'target':'all'},{'opens':'bad'},{'quiet_days':91},{'steps':[]},{'max_24h':4,'max_7d':3},
                       {'steps':[{'campaign_id':999999,'delay_minutes':0}]},{'min_interval_minutes':True}):
            with self.assertRaises(ValueError):self.save(**config)
        row=self.save()
        with self.assertRaises(ValueError):engine.start(row.pk,True)
        for data in ({'action':'advance','minutes':True},{'action':'advance','minutes':10081},{'action':'advance','minutes':-1},
                     {'action':'tick','mode':'real'},{'action':'tick','send':True},{'action':'pause','run_id':False}):
            self.assertEqual(self.post(data).status_code,400)
        self.assertEqual(AutomationSimulationClock.objects.get().clock_at,self.now)

    def test_get_is_read_only_and_report_filters_persisted_logs(self):
        run=self.start();engine.tick(10)
        before=AutomationSimulationAction.objects.count()
        result=self.client.get('/api/automation-simulation',{'run_id':run.pk,'kind':'simulated','query':'ACTIVE','direction':'asc'}).json()
        self.assertFalse(result['can_send']);self.assertEqual(len(result['history']),1)
        self.assertEqual(result['run']['snapshot']['revision'],1)
        self.assertEqual(AutomationSimulationAction.objects.count(),before)
        self.assertEqual(self.client.get('/api/automations').json()['scenarios'][0]['run']['id'],run.pk)

    def test_csrf_and_run_routes_reject_real_mode(self):
        row=self.save()
        browser=HttpClient(enforce_csrf_checks=True)
        self.assertEqual(browser.post('/api/automation-simulation',{'action':'tick'},content_type='application/json').status_code,403)
        r=self.client.post(f'/api/automations/{row.pk}/run',{'revision':1,'mode':'real','test_only':False},content_type='application/json')
        self.assertEqual(r.status_code,400);self.assertEqual(AutomationSimulationRun.objects.count(),0)

    def test_source_statistics_scope_and_size_guard_roll_back_start(self):
        import uuid
        CampaignTestEvent.objects.create(event_id=uuid.uuid4(),campaign=self.campaign,recipient='stat@example.invalid',kind='click',fingerprint='test',occurred_at=self.now)
        run=self.start(source='statistics',source_campaign_id=self.campaign.pk)
        self.assertEqual(list(run.members.values_list('recipient',flat=True)),['stat@example.invalid'])
        row=self.save()
        with patch('contacts.automation_simulation.classification',return_value=({},self.now,[],{},1001,0,'test')):
            with self.assertRaises(ValueError):engine.start(row.pk,1)
        self.assertFalse(row.runs.exists())

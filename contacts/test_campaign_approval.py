from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase,override_settings,RequestFactory
from django.utils import timezone
from .models import (AccountRole,Audience,Client,Address,Contact,EmailTemplate,CampaignSender,EmailCampaign,
    CampaignApproval,CampaignQueue,ActionAuditEntry,AutomationScenario,AutomationSimulationRun)
from .campaign_approval import require_approved,require_steps,signature
from . import automation_simulation as engine,rehearsal
from .dispatch_queue import run_round

User=get_user_model()

@override_settings(SALES_AUTH_REQUIRED=True,PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ApprovalTests(TestCase):
    def setUp(self):
        self.owner=User.objects.create_superuser('owner',password='Fixture-only-123!')
        self.manager=User.objects.create_user('manager');AccountRole.objects.create(user=self.manager,role='manager')
        self.viewer=User.objects.create_user('viewer')
        self.audience=Audience.objects.create(name='Fixture audience')
        self.address=Address.objects.create(email='fixture@example.invalid',validation_result={'code':'mx'})
        c=Contact.objects.create(client=Client.objects.create(name='Fixture',identity_key='fixture'),address=self.address);c.audiences.add(self.audience)
        self.template=EmailTemplate.objects.create(name='Fixture template',subject='Fixture',html='<p>News</p><a href="{{unsubscribe_url}}">Unsubscribe</a>')
        self.sender=CampaignSender.objects.create(name='OVER',email='news@example.invalid')
        self.campaign=EmailCampaign.objects.create(name='Fixture campaign',template=self.template,sender=self.sender,
            template_snapshot={k:getattr(self.template,k) for k in ('id','name','subject','preheader','html','revision')},
            sender_snapshot={k:getattr(self.sender,k) for k in ('id','name','email','reply_to')})
        self.campaign.audiences.add(self.audience);self.url=f'/api/campaigns/{self.campaign.pk}/approval'
        self.client.force_login(self.manager)
    def post(self,action,**kw):
        approval=CampaignApproval.objects.filter(campaign=self.campaign).first();self.campaign.refresh_from_db()
        return self.client.post(self.url,{'action':action,'version':approval.version if approval else 0,'campaign_revision':self.campaign.revision,**kw},content_type='application/json')
    def approve(self):
        self.assertEqual(self.post('submit').status_code,200)
        self.client.force_login(self.owner);self.assertEqual(self.post('approve',comment='Checked').status_code,200)
    def queue(self,action='prepare'):
        return self.client.post(f'/api/campaigns/{self.campaign.pk}/queue',{'action':action,'campaign_revision':self.campaign.revision},content_type='application/json')

    def test_submit_manager_review_admin_and_journal(self):
        response=self.post('submit');self.assertEqual(response.status_code,200)
        data=response.json()['approval'];self.assertEqual(data['status'],'pending');self.assertEqual(data['snapshot']['counts']['ready'],1)
        self.assertEqual(self.post('approve').status_code,403)
        self.client.force_login(self.owner);self.assertEqual(self.post('approve',comment='Approved').status_code,200)
        require_approved(self.campaign,recipients=True)
        event=ActionAuditEntry.objects.get(action='approval_approved');self.assertEqual(event.actor_key,'user:'+str(self.owner.pk))
        self.assertEqual(event.after['comment'],'Approved');self.assertNotIn('html',str(event.after));self.assertFalse(response.json()['can_send'])

    def test_return_needs_comment_and_resubmission_keeps_history(self):
        self.post('submit');self.client.force_login(self.owner)
        self.assertEqual(self.post('return',comment='  ').status_code,400)
        self.assertEqual(self.post('return',comment='Fix sender').status_code,200)
        self.assertEqual(CampaignApproval.objects.get().comment,'Fix sender')
        self.client.force_login(self.manager);self.assertEqual(self.post('submit').status_code,200)
        self.assertEqual(ActionAuditEntry.objects.filter(action='approval_returned').get().after['comment'],'Fix sender')

    def test_viewer_cannot_submit_and_concurrent_decisions_conflict(self):
        self.client.force_login(self.viewer);self.assertEqual(self.post('submit').status_code,403)
        self.client.force_login(self.manager);self.post('submit');self.assertEqual(self.post('submit').status_code,409)
        self.client.force_login(self.owner);version=CampaignApproval.objects.get().version;self.post('approve')
        self.assertEqual(self.post('return',version=version,comment='stale').status_code,409)
        self.assertEqual(CampaignApproval.objects.get().status,'approved')

    def test_campaign_edit_revokes_approval(self):
        self.approve()
        data={'name':'Changed','revision':1,'audience_ids':[self.audience.pk],'template_id':self.template.pk,
            'template_revision':1,'sender_id':self.sender.pk,'schedule_mode':'manual','schedule_timezone':'Asia/Qyzylorda'}
        self.assertEqual(self.client.post(f'/api/campaigns/{self.campaign.pk}',data,content_type='application/json').status_code,200)
        self.assertEqual(CampaignApproval.objects.get().status,'invalidated')
        self.assertEqual(self.queue().status_code,409)
        self.assertTrue(ActionAuditEntry.objects.filter(action='approval_invalidated').exists())

    def test_library_edit_revokes_and_live_sender_change_cannot_bypass(self):
        self.approve()
        self.sender.email='other@example.invalid';self.sender.save()
        self.assertEqual(self.client.get(self.url).json()['approval']['status'],'invalidated')
        self.assertEqual(self.queue().status_code,409)
        self.post('submit');self.post('approve')
        data={'name':'New','subject':'New','preheader':'','html':'<p>New</p>','revision':1,'design':None}
        response=self.client.post(f'/api/templates/{self.template.pk}',data,content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(CampaignApproval.objects.get().status,'invalidated')

    def test_recipient_change_blocks_review_and_prepare_without_modifying_contacts(self):
        self.post('submit');self.address.suppression_reason='Fixture exclusion';self.address.save()
        self.client.force_login(self.owner);self.assertEqual(self.post('approve').status_code,409)
        self.assertEqual(CampaignApproval.objects.get().status,'invalidated')
        self.address.suppression_reason='';self.address.save();self.approve()
        self.address.archived=True;self.address.save();self.assertEqual(self.queue().status_code,409)
        self.address.refresh_from_db();self.assertTrue(self.address.archived)

    def test_queue_worker_stops_after_approval_revoked(self):
        self.assertEqual(self.queue().status_code,409);self.approve()
        self.assertEqual(self.queue().status_code,200);self.assertEqual(self.queue('resume').status_code,200)
        self.post('submit')
        self.assertFalse(run_round(timezone.now()))
        q=CampaignQueue.objects.get();self.assertEqual(q.state,'paused');self.assertIn('approval_stop',q.snapshot)
        self.assertEqual(q.items.filter(attempts__gt=0).count(),0)
        self.assertEqual(self.queue('resume').status_code,409)

    def test_scenarios_and_rehearsals_cannot_bypass_and_stamp_expires(self):
        scenario=engine.save({'mode':'simulation','test_only':True,'name':'Fixture scenario','config':{'target':'active','steps':[{'campaign_id':self.campaign.pk,'delay_minutes':60}]}})
        with self.assertRaises(ValueError):engine.start(scenario.pk,scenario.revision)
        with self.assertRaises(ValueError):rehearsal.start(RequestFactory().get('/'),{'mode':'simulation','test_only':True,'scenario_id':scenario.pk,'revision':scenario.revision})
        self.assertFalse(AutomationSimulationRun.objects.exists());self.approve()
        run=engine.start(scenario.pk,scenario.revision);require_steps(run.snapshot)
        self.post('submit');self.post('approve')
        with self.assertRaises(ValueError):require_steps(run.snapshot)
        with self.assertRaises(ValueError):engine.control(run.pk,'resume')

    def test_csrf_invalid_versions_unknown_actions_and_no_network(self):
        from django.test import Client as HttpClient
        c=HttpClient(enforce_csrf_checks=True);c.force_login(self.owner)
        self.assertEqual(c.post(self.url,{'action':'submit'},content_type='application/json').status_code,403)
        self.assertEqual(self.post('submit',version=True).status_code,409)
        self.assertEqual(self.post('something').status_code,400)
        self.assertEqual(self.post('submit',comment='x'*2001).status_code,400)
        before=list(Address.objects.values())
        with patch('smtplib.SMTP',side_effect=AssertionError('SMTP')),patch('httpx.Client.send',side_effect=AssertionError('HTTP')):
            self.approve();self.client.get(self.url);self.queue()
        self.assertEqual(list(Address.objects.values()),before)

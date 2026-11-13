import io
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase,Client as HttpClient,override_settings
from django.utils import timezone
from . import notifications as n,action_audit as audit
from .models import (AppNotification,NotificationRead,AccountRole,EmailTemplate,CampaignSender,EmailCampaign,
    CampaignApproval,CampaignQueue,CampaignQueueItem,CampaignQueueAttempt,Address,CollectionState)

User=get_user_model()

@override_settings(SALES_AUTH_REQUIRED=True,PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class NotificationTests(TestCase):
    def setUp(self):
        self.admin=User.objects.create_superuser('owner',password='Fixture-only-123!')
        self.manager=User.objects.create_user('manager');AccountRole.objects.create(user=self.manager,role='manager')
        self.viewer=User.objects.create_user('viewer');self.other=User.objects.create_user('other')
        self.client.force_login(self.manager)
    def emit(self,key='fixture',**values):
        return n.emit(key,kind='error',code='fixture_error',title='Fixture',message='Fixture message',**values)
    def post(self,rows,action='read',**extra):
        return self.client.post('/api/notifications',{'action':action,'items':[{'id':r.pk,'version':r.version} for r in rows],**extra},content_type='application/json')
    def campaign(self):
        t=EmailTemplate.objects.create(name='Fixture',html='<p>Fixture</p>');s=CampaignSender.objects.create(name='Fixture',email='fixture@example.invalid')
        return EmailCampaign.objects.create(name='Fixture',template=t,sender=s)

    def test_requires_login_csrf_and_reads_are_per_user(self):
        row=self.emit();self.assertEqual(self.client.get('/api/notifications').json()['unread'],1)
        self.assertEqual(self.post([row]).status_code,200);self.assertEqual(n.unread(self.manager),0)
        self.client.force_login(self.viewer);self.assertEqual(n.unread(self.viewer),1)
        c=HttpClient(enforce_csrf_checks=True);c.force_login(self.viewer)
        self.assertEqual(c.post('/api/notifications',{'action':'read'},content_type='application/json').status_code,403)
        self.client.logout();self.assertEqual(self.client.get('/api/notifications').status_code,401)
        self.assertEqual(NotificationRead.objects.count(),1)
        c=HttpClient();c.force_login(self.manager)
        self.assertEqual(c.get('/api/notifications').json()['unread'],0)

    def test_role_visibility_and_direct_ack_do_not_leak(self):
        public=self.emit('public');admin=self.emit('admin',visibility='admin');ops=self.emit('ops',visibility='operations')
        own=self.emit('own',visibility='admin',owner=self.viewer)
        self.client.force_login(self.viewer)
        self.assertEqual({x['id'] for x in self.client.get('/api/notifications').json()['items']},{public.pk,own.pk})
        self.assertEqual(self.post([public,admin]).status_code,404);self.assertEqual(NotificationRead.objects.count(),0)
        self.client.force_login(self.manager);self.assertEqual(set(n.visible(self.manager).values_list('pk',flat=True)),{public.pk,ops.pk})
        self.client.force_login(self.admin);self.assertEqual(n.visible(self.admin).count(),4)

    def test_dedup_revision_and_stale_ack_cannot_read_new_error(self):
        row=self.emit(fingerprint='one');self.post([row]);self.emit(fingerprint='one')
        self.assertEqual(n.unread(self.manager),0);self.assertEqual(AppNotification.objects.count(),1)
        changed=self.emit(fingerprint='two');self.assertEqual(n.unread(self.manager),1)
        self.assertEqual(self.post([row]).status_code,409)
        self.assertEqual(self.post([changed]).status_code,200)
        self.assertEqual(self.post([changed],action='unread').status_code,200);self.assertEqual(n.unread(self.manager),1)

    def test_pending_decision_resolves_and_historical_sync_is_idempotent(self):
        campaign=self.campaign();approval=CampaignApproval.objects.create(campaign=campaign,status='pending',version=1)
        submitted=audit.record('approval_submitted','campaign',campaign.pk,campaigns=[campaign.pk],after={'version':1})
        pending=AppNotification.objects.get(code='approval_wait');self.assertFalse(pending.resolved)
        self.assertEqual(n.visible(self.viewer).count(),0);self.assertEqual(n.visible(self.admin).count(),1)
        approval.status='returned';approval.version=2;approval.save()
        audit.record('approval_returned','campaign',campaign.pk,campaigns=[campaign.pk],after={'version':2,'comment':'Sensitive details stay in the approval page'})
        pending.refresh_from_db();self.assertTrue(pending.resolved)
        before=list(AppNotification.objects.order_by('pk').values())
        for _ in range(2):call_command('sync_app_notifications',stdout=io.StringIO())
        self.assertEqual(before,list(AppNotification.objects.order_by('pk').values()))
        self.assertEqual(n.unread(self.admin),1)
        self.assertFalse(AppNotification.objects.filter(message__contains='Sensitive').exists())

    def test_stop_and_resume_and_failed_action_messages_are_safe(self):
        stopped=audit.record('auto_stopped','rehearsal',7,campaigns=[3],summary='private-recipient@example.invalid')
        row=AppNotification.objects.get();self.assertEqual(row.target,'rehearsal');self.assertEqual(row.object_id,7)
        self.assertNotIn('private-recipient',row.message)
        audit.record('resumed','rehearsal',7,campaigns=[3]);row.refresh_from_db();self.assertTrue(row.resolved)
        audit.record('attempted','campaign',3,result='failed',summary='password=secret SMTP traceback')
        response=self.client.get('/api/notifications').json();self.assertNotIn('password',str(response));self.assertNotIn('traceback',str(response))
        self.assertEqual(response['unread'],1)
        self.assertEqual(response['items'][0]['kind'],'error')

    def test_source_errors_group_reopen_and_clear_without_copying_raw_error(self):
        state=CollectionState.objects.create(pk=1,error='api_token=fixture')
        row=AppNotification.objects.get();self.assertEqual(row.visibility,'admin');self.assertNotIn('api_token',row.message)
        self.client.force_login(self.admin);self.post([row]);state.save();self.assertEqual(n.unread(self.admin),0)
        state.error='';state.save();row.refresh_from_db();self.assertTrue(row.resolved)
        state.error='api_token=fixture';state.save();row.refresh_from_db();self.assertFalse(row.resolved)
        self.assertEqual(row.version,2);self.assertEqual(n.unread(self.admin),1)
        self.assertEqual(AppNotification.objects.count(),1)

    def test_queue_and_rehearsal_errors_group_and_retry_resolution(self):
        campaign=self.campaign();q=CampaignQueue.objects.create(campaign=campaign)
        address=Address.objects.create(email='fixture@example.invalid')
        item=CampaignQueueItem.objects.create(queue=q,address=address,email=address.email)
        first=CampaignQueueAttempt.objects.create(item=item,number=1,outcome='temporary')
        row=AppNotification.objects.get();self.post([row])
        n.queue_attempt(CampaignQueueAttempt,first,True);self.assertEqual(n.unread(self.manager),0)
        CampaignQueueAttempt.objects.create(item=item,number=2,outcome='permanent')
        self.assertEqual(n.unread(self.manager),1);self.assertEqual(AppNotification.objects.count(),1)
        item.state='simulated';item.save();q.state='completed';q.save();row.refresh_from_db();self.assertTrue(row.resolved)
        audit.record('test_event','rehearsal',4,after={'kind':'temporary_error'},campaigns=[campaign.pk])
        audit.record('test_event','rehearsal',4,after={'kind':'complaint'},campaigns=[campaign.pk])
        self.assertEqual(AppNotification.objects.filter(code='delivery_error').count(),1)

    def test_filters_pagination_validation_and_read_page_only(self):
        rows=[self.emit('row:'+str(i)) for i in range(28)]
        data=self.client.get('/api/notifications?kind=error').json();self.assertEqual(data['pagination']['total'],28);self.assertEqual(len(data['items']),25)
        for query in ('kind=bad','state=bad','page=bad','page=0'):
            self.assertEqual(self.client.get('/api/notifications?'+query).status_code,400)
        self.assertEqual(self.post(rows[:25]).status_code,200);self.assertEqual(n.unread(self.manager),3)
        self.assertEqual(self.client.get('/api/notifications?state=unread').json()['pagination']['total'],3)
        self.assertEqual(self.post([rows[0],rows[0]]).status_code,400)
        self.assertEqual(self.post([rows[0]],action='delete').status_code,400)

    def test_new_approval_request_supersedes_previous_decision(self):
        campaign=self.campaign();a=CampaignApproval.objects.create(campaign=campaign,status='returned',version=2)
        audit.record('approval_returned','campaign',campaign.pk,campaigns=[campaign.pk])
        a.status='pending';a.version=3;a.save()
        audit.record('approval_submitted','campaign',campaign.pk,campaigns=[campaign.pk],after={'version':3})
        self.assertTrue(AppNotification.objects.get(code='approval_decision').resolved)
        self.assertFalse(AppNotification.objects.get(code='approval_wait').resolved)
        self.assertEqual(n.unread(self.admin),1)

    def test_blocked_preparation_is_grouped_and_cancel_resolves(self):
        from .models import AutomationScenario,CampaignRehearsal,RehearsalLog
        s=AutomationScenario.objects.create(name='Fixture',config={})
        r=CampaignRehearsal.objects.create(scenario=s)
        for i in range(2):RehearsalLog.objects.create(rehearsal=r,kind='blocked',detail='password=secret',occurred_at=timezone.now())
        row=AppNotification.objects.get(code='preparation_error');self.assertEqual(row.version,2)
        self.assertEqual(row.target,'rehearsal');self.assertNotIn('secret',row.message)
        audit.record('cancelled','rehearsal',r.pk);row.refresh_from_db();self.assertTrue(row.resolved)

    def test_transaction_rollback_get_no_writes_and_no_network_or_contact_changes(self):
        with self.assertRaises(ValueError):
            with transaction.atomic():
                audit.record('auto_stopped','rehearsal',7);raise ValueError('rollback')
        self.assertEqual(AppNotification.objects.count(),0)
        row=self.emit();address=Address.objects.create(email='fixture@example.invalid');before=list(Address.objects.values())
        with patch('smtplib.SMTP',side_effect=AssertionError('SMTP')),patch('httpx.Client.send',side_effect=AssertionError('HTTP')):
            self.client.get('/api/notifications');self.client.get('/api/notifications')
            self.assertEqual(NotificationRead.objects.count(),0);self.post([row])
        self.assertEqual(list(Address.objects.values()),before)
        self.assertEqual(AppNotification.objects.count(),1)

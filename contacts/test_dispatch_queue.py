from django.test import override_settings as _legacy_auth
from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from django.core.management import call_command
from .models import Audience, Client, Contact, Address, EmailTemplate, CampaignSender, EmailCampaign, CampaignQueue, CampaignQueueItem, CampaignQueueAttempt
from .dispatch_queue import run_round


@_legacy_auth(SALES_AUTH_REQUIRED=False)
class DispatchQueueTests(TestCase):
    def setUp(self):
        self.audience=Audience.objects.create(name='Audience')
        for i in range(2):
            address=Address.objects.create(email=f'queue{i}@example.com',validation_result={'code':'mx'})
            contact=Contact.objects.create(client=Client.objects.create(name=f'Client{i}',identity_key=f'client{i}'),address=address)
            contact.audiences.add(self.audience)
        self.template=EmailTemplate.objects.create(name='Template',subject='Subject',html='<p>Offer</p>')
        self.sender=CampaignSender.objects.create(name='Sender',email='sender@example.kz')
        self.campaign=EmailCampaign.objects.create(name='Campaign',template=self.template,sender=self.sender,
            template_snapshot={'name':'Template','html':'<p>Offer</p>'},sender_snapshot={'email':self.sender.email})
        self.campaign.audiences.add(self.audience)
        self.url=f'/api/campaigns/{self.campaign.pk}/queue'
        self.now=timezone.now()+timedelta(seconds=1)

    def post(self,**data):return self.client.post(self.url,data=data,content_type='application/json')
    def prepare(self,**data):
        result=self.post(action='prepare',campaign_revision=self.campaign.revision,rate_per_minute=60,retry_seconds=5,**data)
        self.assertEqual(result.status_code,200,result.content)
        self.queue=CampaignQueue.objects.get()
        return result.json()['queue']
    def single(self):
        first=self.queue.items.first()
        self.queue.items.exclude(pk=first.pk).delete()
        return first

    def test_prepare_is_paused_and_idempotent_and_deduplicates(self):
        self.prepare()
        self.assertFalse(run_round(self.now))
        self.assertEqual(self.post(action='prepare',campaign_revision=self.campaign.revision).status_code,200)
        self.assertEqual(CampaignQueue.objects.count(),1)
        self.assertEqual(CampaignQueueItem.objects.count(),2)
        self.assertEqual(CampaignQueueAttempt.objects.count(),0)
        self.assertEqual(self.client.get(self.url).json()['queue']['mode'],'simulation')

    def test_speed_pause_resume_and_completion(self):
        self.prepare();self.post(action='resume')
        self.assertTrue(run_round(self.now))
        self.assertFalse(run_round(self.now))
        self.post(action='pause')
        self.assertFalse(run_round(self.now+timedelta(seconds=10)))
        self.post(action='resume')
        self.assertTrue(run_round(self.now+timedelta(seconds=10)))
        self.queue.refresh_from_db();self.assertEqual(self.queue.state,'completed')
        self.assertEqual(CampaignQueueAttempt.objects.count(),2)
        self.assertFalse(run_round(self.now+timedelta(days=1)))

    def test_cancel_keeps_history_and_cannot_resume(self):
        self.prepare();self.post(action='resume');run_round(self.now)
        self.assertEqual(self.post(action='cancel').status_code,200)
        self.assertEqual(self.post(action='cancel').status_code,200)
        self.assertEqual(self.queue.items.filter(state='cancelled').count(),1)
        self.assertEqual(self.queue.items.filter(state='simulated').count(),1)
        self.assertEqual(CampaignQueueAttempt.objects.count(),1)
        self.assertEqual(self.post(action='resume').status_code,409)
        self.assertFalse(run_round(self.now+timedelta(days=1)))

    def test_temporary_once_waits_then_succeeds(self):
        self.prepare(scenario='temporary_once');item=self.single();self.post(action='resume')
        run_round(self.now);item.refresh_from_db()
        self.assertEqual(item.state,'retry');self.assertEqual(item.next_attempt_at,self.now+timedelta(seconds=5))
        self.assertFalse(run_round(self.now+timedelta(seconds=1)))
        self.assertFalse(run_round(self.now+timedelta(seconds=4)))
        self.assertTrue(run_round(self.now+timedelta(seconds=5)))
        item.refresh_from_db();self.assertEqual(item.state,'simulated');self.assertEqual(item.attempts,2)
        self.assertEqual(list(item.history.order_by('number').values_list('outcome',flat=True)),['temporary','simulated'])

    def test_exponential_retry_limit_without_global_suppression(self):
        self.prepare(scenario='temporary_always',max_attempts=3);item=self.single();self.post(action='resume')
        run_round(self.now);run_round(self.now+timedelta(seconds=5));item.refresh_from_db()
        self.assertEqual(item.next_attempt_at,self.now+timedelta(seconds=15))
        self.assertFalse(run_round(self.now+timedelta(seconds=14)))
        run_round(self.now+timedelta(seconds=15));item.refresh_from_db()
        self.assertEqual(item.state,'failed');self.assertEqual(item.attempts,3)
        item.address.refresh_from_db();self.assertEqual(item.address.suppression_reason,'')
        self.assertFalse(run_round(self.now+timedelta(days=1)))

    def test_permanent_error_never_retries_or_blocks_real_address(self):
        self.prepare(scenario='permanent');item=self.single();self.post(action='resume');run_round(self.now)
        item.refresh_from_db();self.assertEqual(item.state,'failed');self.assertEqual(item.attempts,1)
        self.assertFalse(run_round(self.now+timedelta(days=1)))
        item.address.refresh_from_db();self.assertFalse(item.address.archived);self.assertEqual(item.address.suppression_reason,'')

    def test_exclusion_after_preparation_is_checked(self):
        self.prepare();item=self.single()
        Address.objects.filter(pk=item.address_id).update(suppression_reason='Отписка')
        self.post(action='resume');run_round(self.now);item.refresh_from_db()
        self.assertEqual(item.state,'skipped');self.assertEqual(item.attempts,0)
        self.assertEqual(CampaignQueueAttempt.objects.count(),0)

    def test_new_case_variant_block_after_preparation(self):
        self.prepare();item=self.single()
        Address.objects.create(email=item.email.upper(),suppression_reason='Жалоба')
        self.post(action='resume');run_round(self.now);item.refresh_from_db()
        self.assertEqual(item.state,'skipped')

    def test_schedule_prevents_early_processing(self):
        self.campaign.schedule_mode='scheduled';self.campaign.scheduled_at=self.now+timedelta(minutes=1);self.campaign.save()
        self.prepare();self.post(action='resume')
        self.assertFalse(run_round(self.now))
        self.assertTrue(run_round(self.now+timedelta(minutes=1)))

    def test_rate_change_preserves_minimum_gap(self):
        self.prepare();self.post(action='resume');run_round(self.now);self.post(action='pause')
        self.assertEqual(self.post(action='configure',rate_per_minute=1,retry_seconds=5,max_attempts=3,scenario='success').status_code,200)
        self.post(action='resume')
        self.assertFalse(run_round(self.now+timedelta(seconds=59)))
        self.assertTrue(run_round(self.now+timedelta(seconds=60)))

    def test_attempt_limit_can_be_lowered_without_extra_attempt(self):
        self.prepare(scenario='temporary_always');item=self.single();self.post(action='resume');run_round(self.now);self.post(action='pause')
        self.post(action='configure',max_attempts=1,scenario='temporary_always',rate_per_minute=60,retry_seconds=5)
        self.post(action='resume');run_round(self.now+timedelta(seconds=5));item.refresh_from_db()
        self.assertEqual(item.attempts,1);self.assertEqual(item.state,'failed')

    def test_invalid_settings_and_frozen_campaign(self):
        for data in [{'rate_per_minute':0},{'rate_per_minute':601},{'rate_per_minute':True},{'max_attempts':11},{'retry_seconds':0},{'scenario':[]},{'mode':'smtp'}]:
            self.assertEqual(self.post(action='prepare',campaign_revision=1,**data).status_code,409)
        self.assertEqual(self.post(action='prepare',campaign_revision=99).status_code,409)
        self.prepare()
        self.assertEqual(self.client.post(f'/api/campaigns/{self.campaign.pk}',data={'revision':1},content_type='application/json').status_code,409)
        self.post(action='resume')
        self.assertEqual(self.post(action='configure',rate_per_minute=10).status_code,409)
        run_round(self.now);self.post(action='pause')
        self.assertEqual(self.post(action='configure',scenario='permanent').status_code,409)

    @override_settings(SALES_SMTP_ENABLED=True,EMAIL_HOST='smtp.example.com')
    def test_no_transport_even_with_smtp_enabled_and_command_restart(self):
        self.prepare();self.single();self.post(action='resume')
        with patch('django.core.mail.EmailMultiAlternatives.send',side_effect=AssertionError('Mail used')),patch('smtplib.SMTP',side_effect=AssertionError('SMTP used')),patch('socket.create_connection',side_effect=AssertionError('Network used')):
            call_command('process_campaign_queue',once=True,stdout=StringIO())
            call_command('process_campaign_queue',once=True,stdout=StringIO())
        self.assertEqual(CampaignQueueAttempt.objects.count(),1)
        self.assertEqual(self.queue.items.get().state,'simulated')

    def test_snapshot_does_not_grow_when_new_contacts_arrive(self):
        self.prepare()
        address=Address.objects.create(email='new@example.com',validation_result={'code':'mx'})
        contact=Contact.objects.create(client=Client.objects.create(name='New',identity_key='new'),address=address)
        contact.audiences.add(self.audience)
        self.assertEqual(self.queue.items.count(),2)
        self.assertEqual(self.post(action='prepare',campaign_revision=1).json()['queue']['total'],2)

    def test_subscription_centre_revocation_is_checked_even_with_stale_address_cache(self):
        from .subscriptions import request_subscription,apply,unsubscribe_link
        self.prepare();item=self.single()
        subscription,raw,_=request_subscription({'email':item.email,'source':'Website form','is_test':False})
        apply(raw,True)
        _,raw,_=unsubscribe_link(subscription.pk);apply(raw)
        Address.objects.filter(pk=item.address_id).update(suppression_reason='')
        self.post(action='resume');run_round(self.now)
        item.refresh_from_db();self.assertEqual(item.state,'skipped')
        self.assertEqual(CampaignQueueAttempt.objects.count(),0)

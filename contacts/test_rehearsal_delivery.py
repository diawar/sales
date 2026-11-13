from django.test import override_settings as _legacy_auth
import uuid
from unittest.mock import patch
from django.test import TestCase, Client, override_settings
from . import test_rehearsal as fixtures
from .models import (Address, EmailSubscription, EmailExclusion, ActivityTestEvent, CampaignTestEvent,
    TrackingEvent, TrackedMessage, RehearsalDeliveryEvent, AutomationSimulationAction, CampaignQueue)
from . import rehearsal


@override_settings(SALES_PUBLIC_BASE_URL='https://sales.example.com')
@_legacy_auth(SALES_AUTH_REQUIRED=False)
class RehearsalDeliveryTests(TestCase):
    setUp=fixtures.RehearsalTests.setUp
    start=fixtures.RehearsalTests.start
    event=fixtures.RehearsalTests.event
    act=fixtures.RehearsalTests.act

    def result(self,row,kind='temporary_error',letter=None,event_id=None,attempt=None,detail=''):
        letter=letter or row.letters.order_by('pk').first()
        letter.refresh_from_db()
        return self.act(row,'delivery_event',letter_id=letter.pk,kind=kind,attempt=attempt or letter.attempt_count,
            event_id=str(event_id or uuid.uuid4()),detail=detail)

    def test_temporary_backoff_retry_waits_for_outcome_no_auto_success(self):
        row=self.start();letter=row.letters.get();row=self.result(row)
        letter.refresh_from_db();self.assertEqual(letter.delivery_state,'retry')
        self.assertEqual((letter.retry_at-row.clock_at).total_seconds(),900)
        row=self.act(row,'advance',minutes=14);letter.refresh_from_db();self.assertEqual(letter.attempt_count,1)
        row=self.act(row,'advance',minutes=1);letter.refresh_from_db()
        self.assertEqual(letter.delivery_state,'awaiting_result');self.assertEqual(letter.attempt_count,2)
        member=rehearsal.report(row,{})['members'][0]
        self.assertIsNone(member['next_at']);self.assertEqual(member['next_note'],'Ожидается тестовый результат')
        row=self.act(row,'advance',minutes=1440);self.assertEqual(row.letters.count(),1)
        self.assertEqual(TrackedMessage.objects.count(),1);self.assertFalse(TrackingEvent.objects.exists())
        self.assertEqual(row.logs.filter(kind='retry_started').count(),1)

    def test_maximum_attempts_exhaustion_stops_future_steps(self):
        row=self.start();row=self.result(row)
        row=self.act(row,'advance',minutes=15);row=self.result(row)
        letter=row.letters.get();self.assertEqual((letter.retry_at-row.clock_at).total_seconds(),3600)
        row=self.act(row,'advance',minutes=60);row=self.result(row)
        letter.refresh_from_db();self.assertEqual(letter.attempt_count,3);self.assertEqual(letter.delivery_state,'exhausted')
        self.assertEqual(row.members[0]['state'],'blocked');self.assertIsNone(letter.retry_at)
        self.act(row,'advance',minutes=10080);self.assertEqual(row.letters.count(),1)
        self.assertEqual(row.logs.filter(kind='retry_exhausted').count(),1)

    def test_explicit_test_success_unblocks_next_step_and_stays_synthetic(self):
        row=self.start();row=self.result(row);row=self.act(row,'advance',minutes=15)
        row=self.result(row,'test_delivered');self.assertEqual(row.letters.get().delivery_state,'test_delivered')
        self.assertEqual(row.letters.count(),1);row=self.act(row,'advance',minutes=45)
        self.assertEqual(row.letters.count(),2);self.assertFalse(CampaignTestEvent.objects.exists())
        self.assertFalse(TrackingEvent.objects.exists());self.assertIsNone(rehearsal.report(row,{})['delivered'])

    def test_permanent_and_complaint_are_sticky_and_isolated(self):
        for kind in ('permanent_error','complaint'):
            row=self.start();other=self.start();row=self.result(row,kind)
            self.assertEqual(row.members[0]['state'],'excluded')
            self.assertEqual(rehearsal.people(row)['active@example.invalid']['state'],'excluded')
            row=self.act(row,'advance',minutes=10080);self.assertEqual(row.letters.count(),1)
            other=self.act(other,'advance',minutes=60);self.assertEqual(other.letters.count(),2)
        self.assertFalse(EmailExclusion.objects.exists());self.assertFalse(Address.objects.exists())

    def test_idempotency_uuid_and_same_attempt_result(self):
        row=self.start();eid=uuid.uuid4();row=self.result(row,event_id=eid)
        due=row.letters.get().retry_at
        self.result(row,event_id=eid);self.result(row)
        self.assertEqual(RehearsalDeliveryEvent.objects.count(),1);self.assertEqual(row.letters.get().retry_at,due)
        with self.assertRaises(ValueError):self.result(row,'complaint',event_id=eid)
        self.assertEqual(RehearsalDeliveryEvent.objects.count(),1)

    def test_stale_or_conflicting_success_does_not_clear_a_failure(self):
        row=self.start();row=self.result(row)
        with self.assertRaises(ValueError):self.result(row,'test_delivered')
        row=self.act(row,'advance',minutes=15)
        with self.assertRaises(ValueError):self.result(row,'test_delivered',attempt=1)
        row=self.result(row,'permanent_error',attempt=1)
        with self.assertRaises(ValueError):self.result(row,'test_delivered')
        self.assertEqual(row.letters.get().delivery_state,'permanent_error')

    def test_complaint_after_test_delivery_prevents_following_steps(self):
        row=self.start();row=self.result(row,'test_delivered');row=self.result(row,'complaint')
        self.assertEqual(row.members[0]['state'],'excluded')
        self.act(row,'advance',minutes=1440);self.assertEqual(row.letters.count(),1)
        self.assertEqual(row.letters.get().delivery_events.count(),2)
        row=self.result(row,'permanent_error')
        self.assertEqual(row.letters.get().delivery_state,'complaint')
        self.assertIn('жалоба',row.members[0]['detail'])
        self.assertEqual(rehearsal.report(row,{})['delivery_event_counts']['complaint'],1)

    def test_pause_resume_cancel_and_events_while_paused(self):
        row=self.start();row=self.act(row,'pause');row=self.result(row)
        self.assertEqual(row.letters.get().attempt_count,1)
        with self.assertRaises(ValueError):self.act(row,'advance',minutes=15)
        row=self.act(row,'resume');row=self.act(row,'advance',minutes=15)
        self.assertEqual(row.letters.get().attempt_count,2)
        row=self.act(row,'cancel');self.assertEqual(row.letters.get().delivery_state,'stopped')
        with self.assertRaises(ValueError):self.result(row,'temporary_error')
        row=self.result(row,'complaint');self.assertEqual(row.letters.get().delivery_state,'complaint')
        self.assertEqual(row.members[0]['state'],'cancelled')

    def test_unsubscribe_and_changed_audience_cancel_due_retry(self):
        row=self.start();row=self.result(row)
        row=self.act(row,'event',letter_id=row.letters.get().pk,kind='unsubscribe')
        self.assertEqual(row.letters.get().delivery_state,'stopped');self.assertEqual(row.members[0]['state'],'excluded')
        other=self.start(target='passive',opens='none');other=self.result(other)
        other=self.act(other,'event',letter_id=other.letters.get().pk,kind='click')
        self.assertEqual(other.letters.get().delivery_state,'stopped');self.assertEqual(other.members[0]['state'],'exited')

    def test_recheck_real_exclusions_before_retry_without_changing_them(self):
        row=self.start();row=self.result(row)
        address=Address.objects.create(email='active@example.invalid')
        exclusion=EmailExclusion.objects.create(address=address,reason='manual')
        before=list(Address.objects.values());before_ex=list(EmailExclusion.objects.values())
        row=self.act(row,'advance',minutes=15)
        self.assertEqual(row.letters.get().delivery_state,'stopped');self.assertEqual(row.letters.get().attempt_count,1)
        self.assertEqual(list(Address.objects.values()),before);self.assertEqual(list(EmailExclusion.objects.values()),before_ex)

    def test_last_step_still_retries_and_does_not_prepare_another_letter(self):
        row=self.start(steps=[{'campaign_id':self.campaign.pk,'delay_minutes':0}]);self.assertEqual(row.members[0]['state'],'completed')
        row=self.result(row);row=self.act(row,'advance',minutes=15)
        self.assertEqual(row.letters.get().attempt_count,2);self.assertEqual(row.letters.count(),1)
        row=self.result(row,'permanent_error');self.assertEqual(row.members[0]['state'],'excluded')

    def test_large_time_jump_only_starts_one_retry(self):
        row=self.start();row=self.result(row);row=self.act(row,'advance',minutes=10080)
        self.assertEqual(row.letters.get().attempt_count,2);self.assertEqual(row.letters.get().delivery_state,'awaiting_result')
        self.act(row,'tick');self.assertEqual(row.letters.get().attempt_count,2)

    def test_production_and_original_test_sources_untouched_no_network(self):
        Address.objects.create(email='active@example.invalid',validation_result={'code':'mx'})
        EmailSubscription.objects.create(email='active@example.invalid',is_test=False,state='confirmed')
        before=(list(Address.objects.values()),list(EmailSubscription.objects.filter(is_test=False).values()),list(ActivityTestEvent.objects.values()))
        with patch('socket.create_connection',side_effect=AssertionError('network')),patch('smtplib.SMTP',side_effect=AssertionError('SMTP')),patch('httpx.Client.send',side_effect=AssertionError('HTTP')),patch('django.core.mail.send_mail',side_effect=AssertionError('mail')):
            row=self.start();row=self.result(row);row=self.act(row,'advance',minutes=15);row=self.result(row,'complaint')
        self.assertEqual(before,(list(Address.objects.values()),list(EmailSubscription.objects.filter(is_test=False).values()),list(ActivityTestEvent.objects.values())))
        self.assertFalse(EmailExclusion.objects.exists());self.assertFalse(CampaignQueue.objects.exists());self.assertFalse(AutomationSimulationAction.objects.exists())

    def test_api_validation_scoping_filters_audit_and_read_only_get(self):
        row=self.start();other=self.start();letter=row.letters.get()
        payload={'mode':'simulation','test_only':True,'action':'delivery_event','letter_id':letter.pk,'attempt':1,'kind':'temporary_error','event_id':str(uuid.uuid4())}
        self.assertEqual(self.client.post(f'/api/rehearsals/{other.pk}',payload,content_type='application/json').status_code,400)
        self.assertEqual(self.client.post(f'/api/rehearsals/{row.pk}',payload,content_type='application/json').status_code,200)
        before=RehearsalDeliveryEvent.objects.count()
        response=self.client.get(f'/api/rehearsals/{row.pk}?kind=temporary_error&delivery_state=retry')
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['pagination']['total'],1)
        self.assertEqual(len(response.json()['letters']),1);self.assertEqual(RehearsalDeliveryEvent.objects.count(),before)
        audit=self.client.get(f'/api/rehearsals/{row.pk}/letters/{letter.pk}').json()['delivery_events']
        self.assertEqual(audit[0]['event_id'],payload['event_id']);self.assertEqual(audit[0]['attempt'],1)
        for update in ({'event_id':'bad'},{'attempt':True},{'kind':'sent'},{'detail':'x'*501},{'send':True},{'test_only':False}):
            self.assertEqual(self.client.post(f'/api/rehearsals/{row.pk}',payload|update,content_type='application/json').status_code,400)
        self.assertEqual(Client(enforce_csrf_checks=True).post(f'/api/rehearsals/{row.pk}',payload,content_type='application/json').status_code,403)

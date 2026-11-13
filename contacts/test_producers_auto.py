from django.test import override_settings as _legacy_auth
from datetime import timedelta
from unittest.mock import Mock,patch
from django.test import TestCase
from django.utils import timezone
from .test_producers import row,page
from .models import ProducerPilot,Client,Address,Contact,Audience,ClientFact,CollectionLog
from .producers import control,run_round
from .goszakup_client import SourceError

@_legacy_auth(SALES_AUTH_REQUIRED=False)
class AutomaticProducerTests(TestCase):
    def setUp(self):control('start')
    def step(self,rows,more=False,number=1):
        ProducerPilot.objects.filter(pk=1).update(next_attempt_at=None)
        return run_round(Mock(return_value=page(rows,more,number)))
    def test_automatically_saves_each_page_and_deduplicates(self):
        self.step([row(),row()],True)
        self.assertEqual(Client.objects.count(),1)
        self.step([row(),row('999999999999','Beta',email='b@example.com')],False,2)
        state=ProducerPilot.objects.get();self.assertEqual(state.status,'waiting');self.assertTrue(state.enabled)
        self.assertEqual(state.report['organizations'],2);self.assertEqual(state.report['duplicate_rows'],2);self.assertEqual(state.next_page,3)
        self.assertEqual(Client.objects.count(),2);self.assertEqual(Contact.objects.count(),2)
        self.assertGreater(state.next_attempt_at,timezone.now()+timedelta(hours=5))
        self.assertTrue(Audience.objects.filter(name='Производители',parent__name='Бизнес').exists())
    def test_saves_existing_preserving_local_fields_and_blocks(self):
        client=Client.objects.create(name='Manual',identity_key='kz:123456789012',identifier='123456789012',city='Астана',notes='Keep')
        address=Address.objects.create(email='a@example.com',archived=True,suppression_reason='Отписка')
        Contact.objects.create(client=client,address=address)
        self.step([row()]);client.refresh_from_db();address.refresh_from_db()
        self.assertEqual(Client.objects.count(),1);self.assertEqual(client.city,'Астана');self.assertEqual(client.notes,'Keep');self.assertTrue(address.archived)
        self.assertTrue(ClientFact.objects.filter(field='city',value='Алматы',state='alternative').exists())
    def test_pause_during_request_keeps_cursor_and_data(self):
        def reader(number):control('pause');return page([row()])
        self.assertFalse(run_round(reader));state=ProducerPilot.objects.get()
        self.assertEqual(state.next_page,1);self.assertEqual(state.status,'paused');self.assertFalse(Client.objects.exists())
        control('start');self.step([row()]);self.assertEqual(Client.objects.count(),1)
    def test_page_rollback_and_retry_no_duplicates(self):
        with patch('contacts.producers.attach_contacts',side_effect=RuntimeError('Database error')):
            self.step([row()])
        self.assertFalse(Client.objects.exists());self.assertEqual(ProducerPilot.objects.get().next_page,1)
        self.step([row()]);self.assertEqual(Client.objects.count(),1)
    def test_retry_after_waits_without_network(self):
        exc=SourceError('Rate limited');exc.retry_after=600
        run_round(Mock(side_effect=exc));state=ProducerPilot.objects.get()
        self.assertEqual(state.status,'failed');self.assertGreater(state.next_attempt_at,timezone.now()+timedelta(seconds=590))
        reader=Mock(side_effect=AssertionError('No early retry'));self.assertFalse(run_round(reader));reader.assert_not_called()
    def test_single_lease(self):
        ProducerPilot.objects.filter(pk=1).update(lease='other',lease_until=timezone.now()+timedelta(minutes=1))
        reader=Mock();self.assertFalse(run_round(reader));reader.assert_not_called()
    def test_periodic_cycle_restarts_from_page_one(self):
        self.step([row()]);state=ProducerPilot.objects.get();first=state.token
        reader=Mock(return_value=page([row()]))
        ProducerPilot.objects.filter(pk=1).update(next_attempt_at=None)
        run_round(reader);reader.assert_called_once_with(1)
        self.assertNotEqual(ProducerPilot.objects.get().token,first);self.assertEqual(Client.objects.count(),1)
    def test_phone_only_and_disabled_worker(self):
        self.step([row(email=None)]);self.assertIsNone(Contact.objects.get().address)
        control('pause');reader=Mock();self.assertFalse(run_round(reader));reader.assert_not_called()
    def test_journal_filtered_by_source(self):
        CollectionLog.objects.create(source='goszakup',message='Gos event')
        self.step([row()])
        payload=self.client.get('/api/journal',{'source':'eondiris'}).json()
        self.assertGreater(payload['count'],0);self.assertTrue(all(e['source']=='eondiris' for e in payload['events']))
    def test_ui_changes_state_only(self):
        control('pause')
        with patch('contacts.producers.read_page',side_effect=AssertionError('No HTTP in request')):
            self.assertEqual(self.client.post('/api/producers',data='{"action":"start"}',content_type='application/json').status_code,200)
        self.assertFalse(Client.objects.exists())

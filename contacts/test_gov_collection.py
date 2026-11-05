from unittest.mock import Mock,patch
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from .models import GovCollection,Client,Contact,Address,ClientFact,CollectionLog,Audience
from .gov_collection import control,run_round
from .gov_source import parse_card,ContactsNotPublished
from .goszakup_client import SourceError

def row(slug='one',email='public@example.kz'):
    return parse_card(f'<h1>Госорган</h1><div class="contacts-detail"><table><tr><td>Email</td><td>{email}</td></tr><tr><td>Канцелярия</td><td>+7 (7172) 71-74-67</td></tr></table></div>',f'https://www.gov.kz/org/{slug}')

class GovCollectorTests(TestCase):
    def setUp(self):
        control('configure',['https://www.gov.kz/org/one']);control('start')
    def step(self,value=None):
        GovCollection.objects.filter(pk=1).update(next_attempt_at=None)
        return run_round(Mock(return_value=value or row()))
    def test_expanded_list_limit_and_deduplication(self):
        control('pause')
        urls=[f'https://www.gov.kz/org/entry-{i}' for i in range(59)]
        state=control('configure',urls+[urls[0]])
        self.assertEqual(len(state.urls),59)
        self.assertEqual(state.cursor,0)
        with self.assertRaises(ValueError):control('configure',urls*9)
        with self.assertRaises(ValueError):control('configure',urls+['https://evil.example/org/test'])
    def test_append_preserves_cursor_and_report(self):
        self.step();control('pause');before=GovCollection.objects.get()
        state=control('append',['https://www.gov.kz/org/new','https://www.gov.kz/org/one'])
        self.assertEqual(state.cursor,before.cursor)
        self.assertEqual(state.report,before.report)
        self.assertEqual(state.seen,before.seen)
        self.assertEqual(len(state.urls),2)
        self.assertIsNone(state.completed_at)
        control('start');self.assertTrue(self.step(row('new')))
        self.assertEqual(GovCollection.objects.get().cursor,2)
    def test_save_resume_repeat_and_audience(self):
        self.assertTrue(self.step());state=GovCollection.objects.get()
        self.assertEqual(state.cursor,1);self.assertIsNotNone(state.completed_at)
        self.assertGreater(state.next_attempt_at,timezone.now()+timedelta(hours=5))
        self.assertTrue(Contact.objects.get().audiences.filter(parent__name='Государственные структуры',name='Gov.kz').exists())
        self.step();self.assertEqual(Client.objects.count(),1);self.assertEqual(Contact.objects.count(),1)
        self.assertTrue(ClientFact.objects.filter(field='phone_purpose').exists())
    def test_pause_mid_fetch(self):
        def reader(url):control('pause');return row()
        self.assertFalse(run_round(reader));self.assertEqual(GovCollection.objects.get().cursor,0)
        self.assertFalse(Client.objects.exists())
    def test_transaction_rollback(self):
        with patch('contacts.gov_collection.attach_contacts',side_effect=RuntimeError('error')):self.step()
        self.assertFalse(Client.objects.exists());self.assertEqual(GovCollection.objects.get().cursor,0)
        self.step();self.assertEqual(Client.objects.count(),1)
    def test_existing_manual_data_and_suppression(self):
        Client.objects.create(identity_key='gov:one',name='Ручное название',city='Алматы',notes='Keep')
        Address.objects.create(email='public@example.kz',archived=True,suppression_reason='Отписка')
        self.step();client=Client.objects.get()
        self.assertEqual(client.name,'Ручное название');self.assertEqual(client.notes,'Keep');self.assertEqual(client.city,'Алматы')
        self.assertTrue(Address.objects.get().archived);self.assertTrue(ClientFact.objects.filter(field='name',state='alternative').exists())
    def test_same_email_different_organization(self):
        self.step();control('pause');control('configure',['https://www.gov.kz/org/two']);control('start');self.step(row('two'))
        self.assertEqual(Client.objects.count(),2);self.assertEqual(Address.objects.count(),1)
    def test_retries_then_skip_error_without_empty_client(self):
        exc=SourceError('HTTP 429');exc.retry_after=600
        for i in range(3):
            GovCollection.objects.filter(pk=1).update(next_attempt_at=None)
            self.assertFalse(run_round(Mock(side_effect=exc)))
        state=GovCollection.objects.get();self.assertEqual(state.report['failed'],1);self.assertEqual(state.cursor,1)
        self.assertGreater(state.next_attempt_at,timezone.now()+timedelta(seconds=590));self.assertFalse(Client.objects.exists())
        reader=Mock();self.assertFalse(run_round(reader));reader.assert_not_called()
    def test_unpublished_contacts_skip_without_retries(self):
        self.assertTrue(run_round(Mock(side_effect=ContactsNotPublished('No contacts'))))
        state=GovCollection.objects.get()
        self.assertEqual(state.cursor,1)
        self.assertEqual(state.attempts,0)
        self.assertEqual(state.report['without_contacts'],1)
        self.assertNotIn('failed',state.report)
        self.assertFalse(Client.objects.exists())
    def test_single_worker_lease(self):
        GovCollection.objects.filter(pk=1).update(lease='other',lease_until=timezone.now()+timedelta(seconds=60))
        reader=Mock();self.assertFalse(run_round(reader));reader.assert_not_called()
    def test_configuration_and_api_do_not_fetch(self):
        with self.assertRaises(ValueError):control('configure',['https://www.gov.kz/org/two'])
        control('pause')
        with patch('contacts.gov_collection.read_card',side_effect=AssertionError('No HTTP')):
            response=self.client.post('/api/gov',data='{"action":"start"}',content_type='application/json')
            self.assertEqual(response.status_code,200)
        self.assertFalse(Client.objects.exists());self.assertGreater(self.client.get('/api/journal',{'source':'gov'}).json()['count'],0)
    def test_phone_only_client(self):
        self.step(row(email=''));self.assertIsNone(Contact.objects.get().address)

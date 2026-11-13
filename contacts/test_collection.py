from django.test import override_settings as _legacy_auth
from datetime import timedelta
from unittest.mock import Mock,MagicMock,patch
from django.test import TestCase,SimpleTestCase
from django.utils import timezone
import httpx
from .models import CollectionState,CollectionItem,CollectionParticipant,ParticipantOrigin,Contact,Client,Address,Phone,Evidence
from .collection import configure,claim,guard,discover,store_contact,collect_protocol,collect_contact,run_round,pace,cooldown
from .goszakup_client import PublicClient,SourceError,Page
from .goszakup_source import parse_registry_card,parse_protocol_html,LeadPortal,Participant,ProtocolFormatError

def card(identifier='123456789012'):
    return f'<table><tr><td>БИН участника</td><td>{identifier}</td></tr><tr><td>Наименование на рус. языке</td><td>Alpha</td></tr><tr><td>E-mail</td><td>a@example.com</td></tr><tr><td>Телефон</td><td>+7 701 123 45 67; +7 7172 12 34 56</td></tr></table>'

@_legacy_auth(SALES_AUTH_REQUIRED=False)
class SourceTests(SimpleTestCase):
    def test_identity_match_and_phones(self):
        value=parse_registry_card(card(),'123456789012')
        self.assertEqual(value['emails'],['a@example.com'])
        self.assertEqual(len(value['phones']),2)
        with self.assertRaises(SourceError):parse_registry_card(card(),'000000000000')

    def test_protocol_reads_participants_not_customer(self):
        html='<p>Заказчик 111111111111</p><table><tr><th>Наименование поставщика</th><th>БИН</th></tr><tr><td>Alpha</td><td>123456789012</td></tr></table>'
        people,unresolved=parse_protocol_html(html)
        self.assertEqual([p.identifier for p in people],['123456789012'])
        self.assertEqual(unresolved,0)
        with self.assertRaises(ProtocolFormatError):parse_protocol_html('<html>Изменённый шаблон</html>')

    def test_untrusted_url_not_requested(self):
        api=Mock();portal=LeadPortal(api)
        with self.assertRaises(SourceError):portal.read('https://example.com/card')
        api.http.stream.assert_not_called()

    def test_api_rate_limit_has_retry_after(self):
        with PublicClient(transport=httpx.MockTransport(lambda request:httpx.Response(429,headers={'Retry-After':'300'}))) as api:
            api._pace=lambda:None
            with self.assertRaises(SourceError) as result:api.get_json('/api/core/api/public/organizations/')
            self.assertEqual(result.exception.retry_after,300)

    def test_api_rejects_bad_schema_and_foreign_endpoint(self):
        with PublicClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'results':[],'count':'bad'}))) as api:
            api._pace=lambda:None
            with self.assertRaises(SourceError):api.search()
            with self.assertRaises(SourceError):api.get_json('https://example.com/api/core/api/public/organizations/')

@_legacy_auth(SALES_AUTH_REQUIRED=False)
class CollectorTests(TestCase):
    def setUp(self):
        configure('start');self.token=claim()
        self.participant=CollectionParticipant.objects.create(identifier='123456789012',name='Alpha')
        self.value={'name':'Alpha','emails':['a@example.com'],'phones':['+7 701 123 45 67'],'source_url':'https://old.goszakup.gov.kz/ru/registry/show_supplier/1'}

    def test_single_lease_and_pause(self):
        self.assertIsNone(claim())
        configure('pause')
        with self.assertRaises(SourceError):guard(self.token)
        self.assertFalse(run_round(api_factory=Mock(side_effect=AssertionError('No source requests'))))

    def test_server_cooldown_applies_to_all_requests_without_blocking_worker(self):
        exc=SourceError('Rate limited');exc.retry_after=600
        cooldown(exc)
        with patch('contacts.collection.time.sleep') as sleep:
            with self.assertRaises(SourceError) as caught:pace(self.token)
            self.assertGreater(caught.exception.retry_after,590)
            sleep.assert_not_called()

    def test_cooldown_does_not_consume_attempts_of_queued_items(self):
        CollectionState.objects.filter(pk=1).update(lease='',lease_until=None,next_request_at=timezone.now()+timedelta(minutes=5))
        factory=Mock(side_effect=AssertionError('No source requests during cooldown'))
        self.assertFalse(run_round(api_factory=factory))
        self.participant.refresh_from_db();self.assertEqual(self.participant.attempts,0)
        factory.assert_not_called()

    def test_wrong_source_filter_does_not_advance_cursor(self):
        api=Mock();api.search.return_value=Page(1,[{'external_id':1,'system_id':2,'results_publication_date':timezone.now().isoformat()}],None)
        with self.assertRaises(SourceError):discover(api,self.token)
        self.assertFalse(CollectionItem.objects.exists())
        self.assertEqual(CollectionState.objects.get().windows,[])

    def test_dense_window_split(self):
        api=Mock();api.search.return_value=Page(10001,[],True)
        discover(api,self.token)
        self.assertEqual(len(CollectionState.objects.get().windows),8)
        self.assertFalse(CollectionItem.objects.exists())

    def test_stale_worker_cannot_save(self):
        CollectionState.objects.filter(pk=1).update(lease_until=timezone.now()-timedelta(seconds=1))
        new=claim();self.assertNotEqual(new,self.token)
        with self.assertRaises(SourceError):guard(self.token)

    def test_idempotent_contact_preserves_notes_archive_and_evidence(self):
        store_contact(self.participant,self.value)
        client=Client.objects.get();client.notes='Keep';client.save()
        address=Address.objects.get();address.archived=True;address.suppression_reason='Отписка';address.save()
        store_contact(self.participant,self.value)
        self.assertEqual(Contact.objects.count(),1)
        self.assertEqual(Phone.objects.count(),1)
        address.refresh_from_db();client.refresh_from_db()
        self.assertTrue(address.archived);self.assertEqual(address.suppression_reason,'Отписка');self.assertEqual(client.notes,'Keep')
        self.assertEqual(Contact.objects.get().audiences.get().name,'Бизнес')

    def test_phone_only_visible_and_promoted_when_email_appears(self):
        value={**self.value,'emails':[]}
        store_contact(self.participant,value)
        record=self.client.get('/api/contacts').json()['contacts'][0]
        self.assertEqual(record['email'],'');self.assertEqual(record['validation'],'Без email');self.assertEqual(len(record['phones']),1)
        original_id=Contact.objects.get().id
        store_contact(self.participant,self.value)
        self.assertEqual(Contact.objects.count(),1)
        self.assertEqual(Contact.objects.get().id,original_id)
        self.assertEqual(Contact.objects.get().address.email,'a@example.com')

    def test_removed_publication_not_deleted_or_marked_invalid(self):
        store_contact(self.participant,self.value)
        store_contact(self.participant,{**self.value,'emails':[]})
        self.assertEqual(Contact.objects.count(),1)
        self.assertFalse(Evidence.objects.get().current)
        self.assertEqual(Address.objects.get().validation,'Не проверен')

    def test_unrecognized_protocol_requires_review(self):
        item=CollectionItem.objects.create(external_id=1,published_at=timezone.now())
        portal=Mock();portal.protocol.side_effect=ProtocolFormatError('Изменён формат')
        collect_protocol(portal,self.token);item.refresh_from_db()
        self.assertEqual(item.state,'review')

    def test_protocol_records_contact_origin_without_tender_catalog(self):
        item=CollectionItem.objects.create(external_id=1,published_at=timezone.now())
        portal=Mock();portal.protocol.return_value=([Participant('123456789012','Alpha')],0,'https://v3bl.goszakup.gov.kz/files/download_file/1/2/')
        collect_protocol(portal,self.token)
        self.assertEqual(ParticipantOrigin.objects.get().item,item)
        store_contact(self.participant,self.value)
        self.assertEqual(Evidence.objects.count(),2)

    def test_network_error_keeps_retry_without_deleting_contacts(self):
        store_contact(self.participant,self.value)
        portal=Mock();exc=SourceError('Временно недоступно');exc.retry_after=600;portal.contacts.side_effect=exc
        collect_contact(portal,self.token);self.participant.refresh_from_db()
        self.assertEqual(self.participant.state,'retry')
        self.assertGreater(self.participant.next_attempt_at,timezone.now()+timedelta(seconds=590))
        self.assertEqual(Contact.objects.count(),1)

    def test_ui_controls_no_network(self):
        with patch('contacts.goszakup_client.PublicClient',side_effect=AssertionError('UI must not call source')):
            self.assertEqual(self.client.get('/api/collection').status_code,200)
            self.assertEqual(self.client.post('/api/collection',data='{"action":"pause"}',content_type='application/json').status_code,200)

    def test_no_data_race_on_repeated_company_with_different_emails(self):
        store_contact(self.participant,self.value)
        store_contact(self.participant,{**self.value,'emails':['b@example.com']})
        self.assertEqual(Client.objects.count(),1)
        self.assertEqual(Contact.objects.count(),2)
        self.assertFalse(Evidence.objects.get(contact__address__email='a@example.com').current)

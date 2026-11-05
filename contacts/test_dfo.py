from datetime import timedelta
from unittest.mock import Mock,patch
import httpx
from django.test import SimpleTestCase,TestCase
from django.utils import timezone
from .dfo_source import parse_page,read_page
from .dfo_collection import control,run_round,save_rows,root_name
from .goszakup_client import SourceError
from .models import Client,Contact,Address,DfoCollection,ClientFact,CollectionLog


def html(bin='001240001234',name='Example',email='sales@example.kz,info@example.kz',phone='(727) 225-75-75',mobile='+7 701 123 45 67',page=1):
    headers=['Действия','БИН','Наименование (рус.)','Особые данные','ОПФ','Статус (ДФО)','Блокировка (ДФО)','Область','Регион','Адрес','ОПФ','e-mail','Телефон','Мобильный']
    cells=['<a href="/ru/opi/list/123456789/view">Открыть</a>',bin,name,'Организация','ТОО','Активный','Свободно','Алматы г.а.','Район','г. Алматы, ул. Пример, 1','ТОО',email,phone,mobile]
    return '<p>Всего записей 1</p><ul class="pagination"><li class="active">'+str(page)+'</li></ul><table><thead><tr>'+''.join('<th>'+v+'</th>' for v in headers)+'</tr></thead><tbody><tr>'+''.join('<td>'+v+'</td>' for v in cells)+'</tr></tbody></table>'


class DfoSourceTests(SimpleTestCase):
    def test_contacts_only_from_columns_and_leading_zero(self):
        payload=parse_page(html()+'<footer>support@portal.kz +7 777 555 66 77</footer>',1)
        row=payload['rows'][0]
        self.assertEqual(row['identifier'],'001240001234')
        self.assertEqual(row['emails'],['sales@example.kz','info@example.kz'])
        self.assertEqual({p['kind'] for p in row['phones']},{'mobile','landline'})
        self.assertEqual(row['profile']['city'],'Алматы')
        self.assertEqual(row['source_url'],'https://opi.dfo.kz/ru/opi/list/123456789/view')
        self.assertFalse(payload['has_next'])

    def test_bad_contact_remains_source_evidence(self):
        payload=parse_page(html(email='not-an-email',phone='123-45',mobile=''),1)
        row=payload['rows'][0]
        self.assertEqual(row['emails'],[]);self.assertEqual(row['phones'],[])
        self.assertEqual(row['source_fields']['dfo_contacts']['телефон'],'123-45')
        self.assertEqual(len(payload['issues']),2)

    def test_rejects_changed_page_table_bin_and_foreign_link(self):
        for source in [html(page=2),'<h1>CAPTCHA</h1>',html(bin='bad'),html().replace('/ru/opi/list/123456789/view','https://example.com/contact')]:
            with self.assertRaises(SourceError):parse_page(source,1)

    def test_rejects_short_nonfinal_page(self):
        with self.assertRaises(SourceError):parse_page(html().replace('Всего записей 1','Всего записей 100'),1)

    def test_http_limits_and_no_redirect(self):
        for status in (302,403,429):
            transport=httpx.MockTransport(lambda request:httpx.Response(status,headers={'Retry-After':'600','Location':'https://example.com'}))
            with self.assertRaises(SourceError) as error:read_page(1,transport)
            self.assertEqual(error.exception.retry_after,600)
        transport=httpx.MockTransport(lambda request:httpx.Response(200,text=html(),headers={'Content-Type':'text/html'}))
        self.assertEqual(len(read_page(1,transport)['rows']),1)


class DfoCollectionTests(TestCase):
    def setUp(self):control('start')
    def payload(self,**kwargs):return parse_page(html(**kwargs),1)
    def step(self,payload=None):
        DfoCollection.objects.filter(pk=1).update(next_attempt_at=None)
        return run_round(Mock(return_value=payload or self.payload()))

    def test_pilot_saves_and_stops_and_repeat_is_idempotent(self):
        self.assertTrue(self.step())
        state=DfoCollection.objects.get();self.assertFalse(state.enabled);self.assertIsNotNone(state.completed_at)
        self.assertEqual(state.report['organizations'],1);self.assertEqual(state.report['emails'],2)
        self.assertEqual(Contact.objects.count(),2)
        self.assertTrue(Contact.objects.first().audiences.filter(name='ДФО',parent__name='Бизнес').exists())
        self.assertTrue(ClientFact.objects.filter(field='dfo_address').exists())
        control('start');self.assertTrue(self.step());self.assertEqual(Client.objects.count(),1);self.assertEqual(Contact.objects.count(),2)

    def test_stops_after_two_pages_and_respects_interval(self):
        payload=self.payload();payload.update(total=100,has_next=True)
        self.assertTrue(self.step(payload))
        reader=Mock();self.assertFalse(run_round(reader));reader.assert_not_called()
        self.assertTrue(self.step(payload));state=DfoCollection.objects.get()
        self.assertFalse(state.enabled);self.assertEqual(state.next_page,3);self.assertEqual(state.report['processed_pages'],2)

    def test_pause_mid_fetch_and_lease(self):
        def read(page):control('pause');return self.payload()
        self.assertFalse(run_round(read));self.assertFalse(Client.objects.exists());self.assertEqual(DfoCollection.objects.get().next_page,1)
        control('start');DfoCollection.objects.filter(pk=1).update(lease='other',lease_until=timezone.now()+timedelta(minutes=1))
        reader=Mock();self.assertFalse(run_round(reader));reader.assert_not_called()

    def test_manual_fields_and_suppression_preserved(self):
        Client.objects.create(identity_key='kz:001240001234',identifier='001240001234',name='Manual',city='Астана',notes='Keep')
        Address.objects.create(email='sales@example.kz',archived=True,suppression_reason='Отписка')
        self.step();client=Client.objects.get()
        self.assertEqual(client.name,'Manual');self.assertEqual(client.city,'Астана');self.assertEqual(client.notes,'Keep')
        address=Address.objects.get(email='sales@example.kz');self.assertTrue(address.archived);self.assertEqual(address.suppression_reason,'Отписка')
        self.assertTrue(ClientFact.objects.filter(field='name',state='alternative').exists())

    def test_transaction_rolls_back_and_cursor_stays(self):
        with patch('contacts.dfo_collection.attach_contacts',side_effect=RuntimeError('failure')):
            self.assertFalse(self.step())
        self.assertFalse(Client.objects.exists());self.assertEqual(DfoCollection.objects.get().next_page,1)
        self.assertTrue(self.step())

    def test_error_retries_stop_and_keep_cursor(self):
        error=SourceError('DFO HTTP 429');error.retry_after=600
        for _ in range(3):
            DfoCollection.objects.filter(pk=1).update(next_attempt_at=None)
            self.assertFalse(run_round(Mock(side_effect=error)))
        state=DfoCollection.objects.get();self.assertFalse(state.enabled);self.assertEqual(state.next_page,1)
        self.assertGreater(state.next_attempt_at,timezone.now()+timedelta(seconds=590))

    def test_api_does_not_fetch_and_journal_source(self):
        with patch('contacts.dfo_collection.read_page',side_effect=AssertionError('No HTTP')):
            self.assertEqual(self.client.post('/api/dfo',data='{"action":"start"}',content_type='application/json').status_code,200)
        self.assertGreater(self.client.get('/api/journal',{'source':'dfo'}).json()['count'],0)

    def test_automatic_continues_after_pilot_and_past_two_pages(self):
        payload=self.payload();payload.update(total=100,has_next=True)
        self.step(payload);self.step(payload)
        control('start',mode='automatic')
        state=DfoCollection.objects.get();self.assertEqual(state.next_page,3)
        self.assertEqual(state.report['processed_pages'],2)
        self.assertIsNone(state.completed_at)
        self.assertTrue(self.step(payload));state=DfoCollection.objects.get()
        self.assertTrue(state.enabled);self.assertEqual(state.next_page,4)
        self.assertEqual(state.report['processed_pages'],3)
        control('pause');control('start')
        self.assertEqual(DfoCollection.objects.get().next_page,4)

    def test_completed_automatic_cycle_waits_then_restarts(self):
        control('start',mode='automatic');self.assertTrue(self.step())
        state=DfoCollection.objects.get();self.assertTrue(state.enabled)
        self.assertIsNotNone(state.completed_at)
        self.assertGreater(state.next_attempt_at,timezone.now()+timedelta(hours=23))
        reader=Mock();self.assertFalse(run_round(reader));reader.assert_not_called()
        DfoCollection.objects.filter(pk=1).update(next_attempt_at=timezone.now()-timedelta(seconds=1))
        reader=Mock(return_value=self.payload());self.assertTrue(run_round(reader));reader.assert_called_once_with(1)
        self.assertEqual(DfoCollection.objects.get().report['processed_pages'],1)
        self.assertEqual(Client.objects.count(),1)

    def test_classification_uses_explicit_legal_form_not_owner_or_name(self):
        cases=[('Госпредприятие на праве хоз. ведения','Государственные структуры'),
               ('Другие некоммерческие организации','Некоммерческие организации'),
               ('Акционерное общество некоммерческое','Некоммерческие организации'),
               ('Товарищество с ограниченной ответственностью','Бизнес'),
               ('Акционерное общество','Бизнес'),('','Бизнес')]
        for index,(legal,root) in enumerate(cases):
            row=self.payload(bin=f'00000000{index:04}')['rows'][0]
            row['source_fields']['dfo_legal_form']=legal
            self.assertEqual(root_name(row),root)
            save_rows([row])
            self.assertTrue(Contact.objects.filter(client__identifier=row['identifier'],audiences__name='ДФО',audiences__parent__name=root).exists())

    def test_mode_change_rejected_with_live_lease(self):
        DfoCollection.objects.filter(pk=1).update(lease='other',lease_until=timezone.now()+timedelta(minutes=1))
        with self.assertRaises(ValueError):control('start',mode='automatic')
        self.assertEqual(DfoCollection.objects.get().mode,'pilot')

    def test_contact_remarks_are_visible_in_journal(self):
        payload=self.payload();payload['issues']=['Телефон требует проверки: 001240001234']
        self.step(payload)
        self.assertTrue(CollectionLog.objects.filter(source='dfo',level='warning',message__contains='001240001234').exists())

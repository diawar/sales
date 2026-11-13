from django.test import override_settings as _legacy_auth
import io
import json
from django.test import TestCase, Client as HttpClient
from django.core.files.uploadedfile import SimpleUploadedFile
from .models import Audience, Contact, Address, Evidence, Client

@_legacy_auth(SALES_AUTH_REQUIRED=False)
class ImportTests(TestCase):
    def setUp(self):
        self.audience=Audience.objects.get(name='Бизнес')

    def preview(self,text,audience=None):
        return self.client.post('/api/import/preview',{'audience':audience or self.audience.id,'file':SimpleUploadedFile('contacts.csv',text.encode('utf-8-sig'))})

    def commit(self,token):
        return self.client.post('/api/import/commit',data=json.dumps({'token':token}),content_type='application/json')

    def test_preview_does_not_write_contacts_and_commit_is_idempotent(self):
        report=self.preview('Компания;Email\nAlpha;sales@example.com\nAlpha;SALES@example.com').json()
        self.assertEqual(report['stats']['duplicates'],1)
        self.assertEqual(Contact.objects.count(),0)
        self.assertEqual(self.commit(report['token']).json()['created'],1)
        self.assertEqual(self.commit(report['token']).json()['created'],1)
        self.assertEqual(Contact.objects.count(),1)
        self.assertEqual(Evidence.objects.count(),1)

    def test_invalid_row_blocks_entire_batch(self):
        report=self.preview('Компания;Email\nAlpha;a@example.com\nBroken;not-email').json()
        self.assertFalse(report['can_commit'])
        self.assertEqual(self.commit(report['token']).status_code,400)
        self.assertFalse(Contact.objects.exists())

    def test_import_preserves_suppression_notes_and_multiple_audiences(self):
        text='Компания;Email;Источник;URL\nAlpha;a@example.com;CSV;https://example.com/source'
        self.commit(self.preview(text).json()['token'])
        address=Address.objects.get();address.archived=True;address.suppression_reason='Отписка';address.save()
        client=Client.objects.get();client.notes='Сохранить';client.save()
        other=Audience.objects.create(name='Сервисные центры',parent=self.audience)
        report=self.preview(text,other.id).json();self.commit(report['token'])
        address.refresh_from_db();client.refresh_from_db()
        self.assertTrue(address.archived)
        self.assertEqual(address.suppression_reason,'Отписка')
        self.assertEqual(client.notes,'Сохранить')
        self.assertEqual(Contact.objects.get().audiences.count(),2)

    def test_shared_address_does_not_merge_different_clients(self):
        self.commit(self.preview('Компания;Email\nAlpha;a@example.com\nBeta;a@example.com').json()['token'])
        self.assertEqual(Address.objects.count(),1)
        self.assertEqual(Client.objects.count(),2)
        self.assertEqual(Contact.objects.count(),2)

    def test_same_name_different_addresses_not_merged_without_identifier(self):
        self.commit(self.preview('Компания;Email\nAlpha;a@example.com\nAlpha;b@example.com').json()['token'])
        self.assertEqual(Client.objects.count(),2)

    def test_malformed_headers_and_urls_rejected(self):
        self.assertEqual(self.preview('Компания;Email;E-mail\nAlpha;a@example.com;a@example.com').status_code,400)
        report=self.preview('Компания;Email;URL\nAlpha;a@example.com;javascript:alert(1)').json()
        self.assertFalse(report['can_commit'])

    def test_notes_and_audience_persist_across_http_clients(self):
        self.commit(self.preview('Компания;Email\nAlpha;a@example.com').json()['token'])
        contact=Contact.objects.get()
        response=self.client.post(f'/api/contacts/{contact.id}/notes',data=json.dumps({'notes':'После перезапуска'}),content_type='application/json')
        self.assertEqual(response.status_code,200)
        self.assertEqual(HttpClient().get('/api/contacts').json()['contacts'][0]['notes'],'После перезапуска')
        self.assertEqual(self.client.post('/api/audiences',data=json.dumps({'name':'Партнёры','parent_id':self.audience.id}),content_type='application/json').status_code,201)
        self.assertEqual(self.client.post('/api/audiences',data=json.dumps({'name':'партнёры','parent_id':self.audience.id}),content_type='application/json').status_code,400)

    def test_category_contacts_belong_to_parent_without_duplicate_contact(self):
        child=Audience.objects.create(name='Сервисные центры',parent=self.audience)
        self.commit(self.preview('Компания;Email\nAlpha;a@example.com',child.id).json()['token'])
        record=self.client.get('/api/contacts').json()['contacts'][0]
        self.assertEqual(set(record['audience_ids']),{self.audience.id,child.id})
        self.assertEqual(Contact.objects.count(),1)
        self.assertEqual(Audience.objects.filter(parent__isnull=True).count(),4)
        self.assertTrue(Audience.objects.filter(name='Некоммерческие организации',parent__isnull=True).exists())

    def test_xlsx_values_import_and_formulas_rejected(self):
        from openpyxl import Workbook
        workbook=Workbook();sheet=workbook.active
        sheet.append(['Компания','Email','Источник','URL'])
        sheet.append(['Alpha','a@example.com','CSV','https://example.com'])
        buffer=io.BytesIO();workbook.save(buffer)
        response=self.client.post('/api/import/preview',{'audience':self.audience.id,'file':SimpleUploadedFile('contacts.xlsx',buffer.getvalue())})
        self.assertTrue(response.json()['can_commit'])
        sheet['B2']='=1+1';buffer=io.BytesIO();workbook.save(buffer)
        response=self.client.post('/api/import/preview',{'audience':self.audience.id,'file':SimpleUploadedFile('contacts.xlsx',buffer.getvalue())})
        self.assertEqual(response.status_code,400)

    def test_same_category_name_in_different_roots(self):
        other=Audience.objects.get(name='Государственные структуры')
        for parent in [self.audience,other]:
            self.assertEqual(self.client.post('/api/audiences',data=json.dumps({'name':'Образование','parent_id':parent.id}),content_type='application/json').status_code,201)

    def test_phone_classification_and_repeat_import(self):
        text='Компания;Email;Телефон\nAlpha;a@example.com;+7 701 123 45 67 | +7 7172 12 34 56 | непонятно'
        for _ in range(2):
            report=self.preview(text).json()
            self.assertTrue(report['can_commit'])
            self.commit(report['token'])
        phones=self.client.get('/api/contacts').json()['contacts'][0]['phones']
        self.assertEqual(len(phones),3)
        self.assertEqual({p['kind'] for p in phones},{'mobile','landline','unknown'})

    def test_csrf_protection_and_unknown_files(self):
        strict=HttpClient(enforce_csrf_checks=True)
        self.assertEqual(strict.post('/api/audiences',data='{"name":"X"}',content_type='application/json').status_code,403)
        self.assertEqual(self.client.get('/settings.py').status_code,404)

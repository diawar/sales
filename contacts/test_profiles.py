from django.test import override_settings as _legacy_auth
import io
import json
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from .models import Audience,Client,Contact,Address,ClientFact
from .profiles import apply_profile

@_legacy_auth(SALES_AUTH_REQUIRED=False)
class ProfilesTests(TestCase):
    def setUp(self):self.audience=Audience.objects.get(name='Бизнес',parent=None)
    def post(self,url,value):return self.client.post(url,data=json.dumps(value),content_type='application/json')
    def create(self,**kwargs):return self.post('/api/clients',{'name':'Alpha','audience_id':self.audience.id,**kwargs})
    def preview(self,text,audience=None):return self.client.post('/api/import/preview',{'audience':audience or self.audience.id,'file':SimpleUploadedFile('profile.csv',text.encode('utf-8-sig'))})
    def commit(self,token):return self.post('/api/import/commit',{'token':token})

    def test_manual_full_card_and_multiple_values_persist(self):
        response=self.create(city='Алматы',industry='IT',websites=['https://example.com'],socials=[{'service':'Telegram','value':'@example'}],tags=['Сервис','Партнёр'],emails=['a@example.com','b@example.com'],phones=['+7 701 123 45 67'],contact_people=[{'name':'Иван','role':'Закупки','email':'person@example.com','phone':''}],notes='Проверить')
        self.assertEqual(response.status_code,200)
        self.assertEqual(Contact.objects.count(),2)
        client=Client.objects.get();self.assertEqual(client.city,'Алматы');self.assertEqual(client.notes,'Проверить')
        records=self.client.get('/api/contacts').json()['contacts']
        self.assertEqual(records[0]['socials'][0]['service'],'Telegram')
        self.assertEqual(len(records[0]['all_emails']),2)
        self.assertTrue(ClientFact.objects.filter(field='city',source='Вручную').exists())

    def test_invalid_link_rolls_back_entire_create(self):
        self.assertEqual(self.create(websites=['javascript:alert(1)']).status_code,400)
        self.assertFalse(Client.objects.exists())
        self.assertEqual(self.create(socials=[{'service':'Telegram','value':'javascript:alert(1)'}]).status_code,400)

    def test_same_identifier_does_not_create_manual_duplicate(self):
        self.assertEqual(self.create(identifier='123456789012').status_code,200)
        self.assertEqual(self.create(identifier='123456789012').status_code,409)
        self.assertEqual(Client.objects.count(),1)

    def test_physical_person_without_email_and_no_iin(self):
        audience=Audience.objects.get(name='Физические лица',parent=None)
        response=self.post('/api/clients',{'name':'Анна','audience_id':audience.id,'city':'Астана','socials':[{'service':'Instagram','value':'@example'}]})
        self.assertEqual(response.status_code,200)
        self.assertIsNone(Contact.objects.get().address)
        self.assertEqual(self.post('/api/clients',{'name':'Другой','audience_id':audience.id,'identifier':'123456789012'}).status_code,400)

    def test_enrichment_conflict_retains_manual_value_and_history(self):
        contact_id=self.create(identifier='123456789012',city='Алматы',emails=['a@example.com']).json()['contact_id']
        client=Client.objects.get()
        self.assertEqual(apply_profile(client,{'city':'Астана','websites':['https://example.com']},'Другой источник'),['city'])
        client.refresh_from_db();self.assertEqual(client.city,'Алматы');self.assertEqual(client.websites,['https://example.com'])
        self.assertTrue(client.facts.filter(field='city',state='alternative',value='Астана').exists())
        self.assertEqual(self.post(f'/api/contacts/{contact_id}/profile',{'city':'Астана'}).status_code,200)
        client.refresh_from_db();self.assertEqual(client.city,'Астана')
        self.assertTrue(client.facts.filter(field='city',state='superseded',value='Алматы').exists())

    def test_import_expanded_fields_and_same_bin_enrichment(self):
        text='Компания;Email;БИН;Город;Сайт;Соцсети;Отрасль;Тип организации;Теги;Контактное лицо;Должность;Email контакта;Телефон контакта\nAlpha;a@example.com;123456789012;Алматы;https://example.com;Telegram=@example;IT;ТОО;Опт|Сервис;Иван;Закупки;person@example.com;'
        response=self.preview(text);self.assertTrue(response.json()['can_commit'])
        self.commit(response.json()['token'])
        second=self.preview('Компания;Email;БИН;Город;Сайт\nAlpha;b@example.com;123456789012;Астана;https://example.org').json()
        self.commit(second['token'])
        self.assertEqual(Client.objects.count(),1);self.assertEqual(Contact.objects.count(),2)
        client=Client.objects.get();self.assertEqual(client.city,'Алматы');self.assertEqual(len(client.websites),2);self.assertEqual(len(client.contact_people),1)
        self.assertTrue(client.facts.filter(field='city',state='alternative').exists())

    def test_existing_blocks_and_notes_preserved_when_profile_saved(self):
        contact_id=self.create(emails=['a@example.com'],notes='Сохранить').json()['contact_id']
        address=Address.objects.get();address.archived=True;address.suppression_reason='Отписка';address.save()
        response=self.post(f'/api/contacts/{contact_id}/profile',{'city':'Алматы','emails':['a@example.com','new@example.com']})
        self.assertEqual(response.status_code,200);address.refresh_from_db();self.assertTrue(address.archived);self.assertEqual(address.suppression_reason,'Отписка');self.assertEqual(Client.objects.get().notes,'Сохранить')

    def test_import_name_only_not_merged_by_name(self):
        report=self.preview('Компания;Город\nAlpha;Алматы').json();self.assertTrue(report['can_commit']);self.assertEqual(report['stats']['unidentified'],1);self.commit(report['token'])
        report=self.preview('Компания;Город\nAlpha;Алматы').json();self.commit(report['token'])
        self.assertEqual(Client.objects.count(),2)

    def test_legacy_value_is_preserved_before_manual_edit(self):
        client=Client.objects.create(name='Old',identity_key='legacy')
        apply_profile(client,{'name':'New'},'Вручную',manual=True)
        self.assertTrue(client.facts.filter(field='name',value='Old',state='superseded').exists())

    def test_hashtag_space_input_and_case_insensitive_duplicates(self):
        response=self.create(tags=['#Опт #Партнёр','опт'])
        self.assertEqual(response.status_code,200)
        self.assertEqual(Client.objects.get().tags,['Опт','Партнёр'])

    def test_enrichment_does_not_duplicate_tag_case_variants(self):
        self.create(tags=['Опт'])
        client=Client.objects.get();apply_profile(client,{'tags':['опт','Партнёр']},'Источник')
        client.refresh_from_db();self.assertEqual(client.tags,['Опт','Партнёр'])

    def test_list_omits_history_and_detail_keeps_sources_and_latest_facts(self):
        contact_id=self.create(city='Алматы',emails=['a@example.com','b@example.com']).json()['contact_id']
        company=Client.objects.get()
        for i in range(105):
            ClientFact.objects.create(client=company,field='city',value=str(i),fingerprint=str(i),source='Проверка')
        rows=self.client.get('/api/contacts').json()['contacts']
        self.assertNotIn('field_history',rows[0])
        self.assertNotIn('contact_sources',rows[0])
        detail=self.client.get(f'/api/contacts/{contact_id}/detail').json()
        self.assertEqual(detail['city'],'Алматы')
        self.assertEqual(set(detail['all_emails']),{'a@example.com','b@example.com'})
        self.assertEqual(len(detail['contact_sources']),2)
        self.assertEqual(len(detail['field_history']),100)
        self.assertEqual(detail['field_history'][0]['value'],'104')
        self.assertEqual(self.client.get('/api/contacts/999999/detail').status_code,404)

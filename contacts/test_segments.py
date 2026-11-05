from django.test import TestCase
from .models import Client,Contact,Address


class SegmentTests(TestCase):
    def post(self,**payload):return self.client.post('/api/segments',payload,content_type='application/json')

    def test_dynamic_preview_and_duplicates(self):
        saved=self.post(name='Город',filters={'city':'Алматы'}).json()
        self.assertEqual(saved['counts']['clients'],0)
        client=Client.objects.create(name='Test',identity_key='segment:test',city='Алматы')
        for i in range(2):Contact.objects.create(client=client,address=Address.objects.create(email=f't{i}@example.com'))
        result=self.post(action='preview',id=saved['id']).json()
        self.assertEqual(result['counts'],{'contacts':2,'clients':1,'emails':2})
        self.assertEqual(self.post(name='ГОРОД',filters={}).status_code,400)

    def test_update_archive_restore_and_invalid(self):
        saved=self.post(name='Проверка',filters={'email_domain':'mail.ru'}).json()
        self.assertEqual(self.post(id=saved['id'],name='Новое',filters={'completeness':'missing_both'}).status_code,200)
        self.post(action='archive',id=saved['id'])
        self.assertEqual(self.client.get('/api/segments').json()['segments'],[])
        self.post(action='restore',id=saved['id'])
        self.assertEqual(len(self.client.get('/api/segments').json()['segments']),1)
        for filters in [{'ids':'1'},{'page':'2'},{'city':[]},{'completeness':'wrong'},{'email_domain':'bad domain'}]:
            self.assertEqual(self.post(name='Bad',filters=filters).status_code,400)

    def test_or_exclusions_and_page_export_agree(self):
        import json,csv,io
        for index,city in enumerate(['Алматы','Астана','Шымкент']):
            client=Client.objects.create(name=f'Client {index}',identity_key=f'rules:{index}',city=city,tags=['опт'] if index==1 else [])
            Contact.objects.create(client=client,address=Address.objects.create(email=f'rules{index}@example.com'))
        criteria={'$or':[{'city':'Алматы'},{'city':'Астана'},{'email_domain':'example.com'}], '$exclude':[{'profile_tag':'опт'}]}
        saved=self.post(name='Города',filters=criteria).json()
        self.assertEqual(saved['counts']['clients'],2)
        page=self.client.get('/api/contacts/page',{'segment_rules':json.dumps(criteria)}).json()
        self.assertEqual(page['pagination']['total'],2)
        self.assertEqual([row['name'] for row in page['contacts']],['Client 0','Client 2'])
        response=self.client.get('/api/contacts/export',{'segment_rules':json.dumps(criteria)})
        exported=list(csv.reader(io.StringIO(b''.join(response.streaming_content).decode('utf-8-sig')),delimiter=';'))
        self.assertEqual([row[0] for row in exported[1:]],['Client 0','Client 2'])
        self.assertEqual(self.client.get('/api/contacts/page',{'segment_rules':json.dumps(criteria),'city':'Алматы'}).json()['pagination']['total'],1)

    def test_exclusion_removes_other_addresses_of_client_and_limits(self):
        client=Client.objects.create(name='Shared',identity_key='shared:rules')
        for email in ['one@mail.ru','two@example.com']:Contact.objects.create(client=client,address=Address.objects.create(email=email))
        self.assertEqual(self.post(action='preview',filters={'$exclude':[{'email_domain':'mail.ru'}]}).json()['counts']['clients'],0)
        for criteria in [{'$or':[]},{'$or':[{}]},{'$or':[{'$or':[{'city':'x'}]}]},{'$or':[{'city':'x'}]*9},{'$exclude':[{'archive':'1'}]}]:
            self.assertEqual(self.post(name='Bad',filters=criteria).status_code,400)
        self.assertEqual(self.client.get('/api/contacts/page',{'segment_rules':'not json'}).status_code,400)

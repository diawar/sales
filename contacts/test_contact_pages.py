from django.test import TestCase
from .models import Address, Audience, Client, Contact, Evidence, Phone


class ContactPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.root=Audience.objects.get(name='Бизнес',parent=None)
        cls.child=Audience.objects.create(name='Проверка страниц',parent=cls.root)
        cls.records=[]
        for i in range(43):
            client=Client.objects.create(name=f'Company {i:03}',identity_key=f'page:{i}',
                city='Алматы' if i%2 else 'Астана',tags=['Партнёр'] if i%2 else [])
            address=Address.objects.create(email=f'page{i}@example.com',archived=(i==42),
                suppression_reason='Отписка' if i==41 else '')
            contact=Contact.objects.create(client=client,address=address)
            contact.audiences.add(cls.root,cls.child)
            Evidence.objects.create(contact=contact,source='Source A',source_url='https://example.com/a')
            Evidence.objects.create(contact=contact,source='Source A',source_url='https://example.com/b')
            Phone.objects.create(client=client,number=f'+7701000{i:04}',raw=f'+7701000{i:04}',kind='mobile',source='Source A')
            cls.records.append(contact)

    def get_page(self,**params):
        response=self.client.get('/api/contacts/page',params)
        self.assertEqual(response.status_code,200)
        return response.json()

    def test_all_pages_are_complete_unique_and_stable(self):
        first=self.get_page(page_size=20)
        second=self.get_page(page=2,page_size=20)
        third=self.get_page(page=3,page_size=20)
        ids=[r['id'] for payload in [first,second,third] for r in payload['contacts']]
        self.assertEqual(ids,[c.id for c in self.records[:-1]])
        self.assertEqual(first['pagination']['total'],42)
        self.assertEqual(len(set(ids)),42)
        self.assertEqual(self.get_page(page=999)['pagination']['page'],3)

    def test_email_domain_exact_match_and_export(self):
        for index,email in enumerate(['target@MAIL.RU','other@notmail.ru','sub@sub.mail.ru']):
            address=Address.objects.create(email=email)
            self.records[index].address=address
            self.records[index].save()
        page=self.get_page(email_domain=' @MAIL.RU ',source='Source A')
        self.assertEqual([r['id'] for r in page['contacts']],[self.records[0].id])
        response=self.client.get('/api/contacts/export',{'email_domain':'mail.ru'})
        body=b''.join(response.streaming_content).decode('utf-8-sig')
        self.assertIn('target@MAIL.RU',body)
        self.assertNotIn('other@notmail.ru',body)
        self.assertNotIn('sub@sub.mail.ru',body)
        for value in ['https://mail.ru','a@mail.ru','mail ru']:
            self.assertEqual(self.client.get('/api/contacts/page',{'email_domain':value}).status_code,400)

    def test_completeness_uses_whole_client_and_export_filters(self):
        empty_client=Client.objects.create(name='Empty target',identity_key='empty-target')
        empty=Contact.objects.create(client=empty_client)
        empty.audiences.add(self.child)
        # A null-address row does not mean that the client has no email.
        Contact.objects.create(client=self.records[0].client)
        phone_client=Client.objects.create(name='Phone only',identity_key='phone-only')
        Contact.objects.create(client=phone_client)
        Phone.objects.create(client=phone_client,number='+77011112233',raw='+77011112233',kind='mobile',source='Test')
        payload=self.get_page(completeness='missing_both',query='target',audience=self.child.id)
        self.assertEqual([r['id'] for r in payload['contacts']],[empty.id])
        self.assertEqual(self.get_page(completeness='with_email',query='Company 000')['pagination']['total'],2)
        response=self.client.get('/api/contacts/export',{'completeness':'missing_both','query':'target'})
        csv=b''.join(response.streaming_content).decode('utf-8-sig')
        self.assertIn('Empty target',csv)
        self.assertNotIn('Phone only',csv)
        coverage=self.client.get('/api/contacts/summary').json()['coverage']
        self.assertEqual(coverage,{'clients':45,'with_email':43,'with_phone':44,'missing_both':1})
        self.assertEqual(self.client.get('/api/contacts/page',{'completeness':'bad'}).status_code,400)

    def test_combined_filters_before_pagination_and_no_join_duplicates(self):
        payload=self.get_page(audience=self.root.id,source='Source A',city='Алматы',
            profile_tag='#партн',phone_kind='mobile',page_size=10,direction='desc')
        self.assertEqual(payload['pagination']['total'],21)
        self.assertEqual(len(payload['contacts']),10)
        self.assertEqual(payload['contacts'][0]['name'],'Company 041')
        self.assertEqual(self.get_page(query='page37@')['contacts'][0]['id'],self.records[37].id)

    def test_exclusions_empty_results_and_metrics(self):
        self.assertEqual(self.get_page(activity='Исключены')['pagination']['total'],1)
        self.assertEqual(self.get_page(activity='Исключены',archive=1)['pagination']['total'],2)
        self.assertEqual(self.get_page(activity='Мало данных')['pagination']['total'],41)
        for params in ({'query':'nothing'},{'delivered':1},{'activity':'Активные'}):
            payload=self.get_page(**params)
            self.assertEqual(payload['contacts'],[])
            self.assertEqual(payload['pagination']['pages'],1)

    def test_invalid_parameters_are_rejected(self):
        for params in ({'page':0},{'page_size':101},{'page':'bad'},{'sort':'client__notes'},
                       {'direction':'bad'},{'archive':'bad'},{'phone_kind':'bad'}):
            self.assertEqual(self.client.get('/api/contacts/page',params).status_code,400)

    def test_legacy_list_is_still_complete(self):
        self.assertEqual(len(self.client.get('/api/contacts').json()['contacts']),43)

    def test_summary_counts_entire_database_and_root_once(self):
        summary=self.client.get('/api/contacts/summary').json()
        self.assertEqual(summary['stats']['total'],43)
        self.assertEqual(summary['stats']['working'],42)
        self.assertEqual(summary['sources'],['Source A'])
        root=summary['audience_counts'][str(self.root.id)]
        self.assertEqual(root['total'],43)
        self.assertEqual(root['segments']['Исключены'],2)
        self.assertEqual(root['segments']['Мало данных'],41)

    def test_export_includes_all_pages_and_selected_subset(self):
        import csv,io
        def export(**params):
            response=self.client.get('/api/contacts/export',params)
            self.assertEqual(response.status_code,200)
            return list(csv.reader(io.StringIO(b''.join(response.streaming_content).decode('utf-8-sig')),delimiter=';'))
        all_rows=export(page=1,page_size=10)
        self.assertEqual(len(all_rows),43) # 42 visible contacts plus header
        self.assertEqual(all_rows[-1][0],'Company 041')
        selected=export(ids=f'{self.records[2].id},{self.records[40].id}')
        self.assertEqual([row[0] for row in selected[1:]],['Company 002','Company 040'])
        filtered=export(city='алматы')
        self.assertEqual(len(filtered),22)

    def test_all_table_sort_columns_and_unicode_search(self):
        for key in ('name','email','audience','activity','validation','delivered','opens','clicks','source','phone','city','profile_type','profile_tag'):
            payload=self.get_page(sort=key,direction='desc',page_size=10)
            self.assertEqual(len(payload['contacts']),10)
        company=self.records[0].client
        company.name='Проверка Кириллицы';company.save()
        self.assertEqual(self.get_page(query='проверка кириллицы')['pagination']['total'],1)

    def test_detail_contains_context_for_contact_outside_first_page(self):
        response=self.client.get(f'/api/contacts/{self.records[35].id}/detail')
        self.assertEqual(response.status_code,200)
        detail=response.json()
        self.assertIn(self.root.id,detail['audience_ids'])
        self.assertEqual(detail['id'],self.records[35].id)
        self.assertEqual(detail['phones'][0]['kind'],'mobile')

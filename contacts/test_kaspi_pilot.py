from django.test import TestCase,SimpleTestCase
from .kaspi_pilot import import_samples,parse_samples
from .kaspi_sample import parse_seller_html
from .models import Client,Contact,Phone


def sample(identifier='123',category='Процессоры'):
    return {'source_url':f'https://kaspi.kz/shop/m/{identifier}/products/',
        'html':'<h3 class="merchant-profile__title">Магазин в городе Алматы</h3><div class="merchant-contacts-informer"><div class="merchant-contacts-informer__row"><svg data-test-id="icon-phone"></svg><span class="merchant-contacts-informer__text">+7 (701) 123-45-67</span></div><div class="merchant-contacts-informer__row"><svg data-test-id="icon-pin"></svg><span class="merchant-contacts-informer__text">Казахстан, г.Шымкент, улица Примерная, 1</span></div></div>',
        'categories':['Компьютеры',category],'discovery_url':'https://kaspi.kz/shop/p/test-123/'}


class KaspiParsingTests(SimpleTestCase):
    def test_named_id_and_address_city_are_preserved(self):
        row=sample('Pulser');parsed=parse_seller_html(row['html'],row['source_url'])
        self.assertEqual(parsed['merchant_id'],'Pulser')
        self.assertEqual(parsed['city'],'Шымкент')
        self.assertEqual(parsed['view_city'],'Алматы')

    def test_view_city_is_not_used_without_address(self):
        parsed=parse_seller_html('<h3 class="merchant-shop-profile__title">Shop в городе Алматы</h3>')
        self.assertEqual(parsed['city'],'')
        self.assertEqual(parsed['view_city'],'Алматы')

    def test_bad_domain_and_missing_contacts_rejected(self):
        row=sample();row['source_url']='https://kaspi.kz.evil.example/shop/m/123/'
        with self.assertRaises(ValueError):parse_samples([row])
        row=sample();row['html']='<h3 class="merchant-profile__title">Shop</h3>'
        with self.assertRaises(ValueError):parse_samples([row])


class KaspiImportTests(TestCase):
    def test_repeated_merchant_combines_categories_without_duplicates(self):
        rows=[sample(),sample(category='Системные блоки')]
        result=import_samples(rows)
        self.assertEqual(result['merchants'],1)
        self.assertEqual(Client.objects.count(),1)
        self.assertTrue(import_samples(rows)['repeated'])
        self.assertEqual(Phone.objects.count(),1)
        self.assertEqual(Contact.objects.count(),1)
        self.assertEqual(Client.objects.get().city,'Шымкент')
        names=set(Contact.objects.get().audiences.values_list('name',flat=True))
        self.assertTrue({'Процессоры','Системные блоки'}.issubset(names))

    def test_same_shop_name_and_phone_do_not_merge_different_merchant_ids(self):
        import_samples([sample('123'),sample('456')])
        self.assertEqual(Client.objects.count(),2)
        self.assertFalse(Contact.objects.exclude(address=None).exists())
        response=self.client.get('/api/kaspi?category=Процессоры')
        self.assertEqual(len(response.json()['rows']),2)
        self.assertEqual(self.client.post('/api/kaspi').status_code,400)

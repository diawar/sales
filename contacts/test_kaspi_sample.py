from django.test import override_settings as _legacy_auth
from django.test import SimpleTestCase
from .kaspi_sample import parse_seller_html

@_legacy_auth(SALES_AUTH_REQUIRED=False)
class KaspiSampleTests(SimpleTestCase):
    def test_user_sample_title_phone_address(self):
        html='<h3 class="merchant-shop-profile__title">MYSKILL в городе Алматы</h3><div class="merchant-contacts-informer"><div class="merchant-contacts-informer__row"><svg data-test-id="icon-phone"></svg><span class="merchant-contacts-informer__text">+7 (702) 066-48-48</span></div><div class="merchant-contacts-informer__row"><svg data-test-id="icon-pin"></svg><span class="merchant-contacts-informer__text">Казахстан, г.Алматы, Алмалинский район, УЛИЦА МУКАНОВА, 245</span></div></div>'
        result=parse_seller_html(html)
        self.assertEqual(result['shop_name'],'MYSKILL');self.assertEqual(result['city'],'Алматы')
        self.assertEqual(result['phones'][0]['number'],'+77020664848');self.assertEqual(result['phones'][0]['kind'],'mobile')
        self.assertIn('МУКАНОВА',result['addresses'][0]);self.assertIsNone(result['merchant_id']);self.assertIsNone(result['email'])

    def test_no_merchant_id_invented_and_support_contacts_ignored(self):
        result=parse_seller_html('<span>Поддержка +7 701 123 45 67</span>','https://kaspi.kz/shop/info/merchant/123/address-tab/')
        self.assertEqual(result['merchant_id'],'123');self.assertEqual(result['phones'],[]);self.assertEqual(result['shop_name'],'')

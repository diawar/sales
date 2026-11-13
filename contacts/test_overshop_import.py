from django.test import override_settings as _legacy_auth
from django.test import TestCase
from .overshop_import import import_pilot
from .models import Client, Contact, Address, ImportBatch


@_legacy_auth(SALES_AUTH_REQUIRED=False)
class OverShopPilotTests(TestCase):
    def row(self,identifier='42'):
        return {'source_customer_id':identifier,'identity_key':'overshop:user:'+identifier,
                'category':'person_candidate','issues':[],'name':'Client','email':'client@example.com',
                'phones':['+77011234567'],'city':'','purchase_segment':'no_orders',
                'orders_total':0,'orders_paid':0,'orders_cancelled':0}

    def test_repeat_and_shared_email_do_not_merge_source_ids(self):
        result=import_pilot([self.row()],'hash')
        self.assertEqual(result['created'],1)
        self.assertTrue(import_pilot([self.row()],'hash')['repeated'])
        import_pilot([self.row('43')],'other')
        self.assertEqual(Client.objects.count(),2)
        self.assertEqual(Address.objects.count(),1)
        self.assertEqual(Contact.objects.count(),2)
        self.assertEqual(ImportBatch.objects.count(),2)

    def test_manual_fields_and_suppression_preserved(self):
        client=Client.objects.create(identity_key='overshop:user:42',name='Manual',city='Manual city')
        Address.objects.create(email='client@example.com',archived=True,suppression_reason='Отписка')
        import_pilot([self.row()],'hash')
        client.refresh_from_db()
        self.assertEqual(client.name,'Manual')
        self.assertEqual(client.city,'Manual city')
        self.assertTrue(Address.objects.get().archived)
        self.assertEqual(Address.objects.get().suppression_reason,'Отписка')

    def test_excluded_row_aborts_entire_pilot(self):
        bad=self.row('43');bad['category']='organization_review'
        with self.assertRaises(ValueError):import_pilot([self.row(),bad],'hash')
        self.assertFalse(Client.objects.exists())
        self.assertFalse(ImportBatch.objects.exists())

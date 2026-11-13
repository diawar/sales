from django.test import override_settings as _legacy_auth
from django.test import TestCase
from .overshop_sync import import_snapshot,subscription_state
from .models import Client,Contact,Address,Audience,OverShopSubscription,ImportBatch


def customer(identifier='42',email='client@example.com',category='person_candidate',name='Client'):
    return {'source_customer_id':identifier,'identity_key':'overshop:user:'+identifier,
            'category':category,'issues':[],'name':name,'email':email,'phones':['+77011234567'],
            'city':'','registered_at':'2023-01-01','purchase_segment':'no_orders',
            'orders_total':0,'orders_paid':0,'orders_cancelled':0}


def subscriber(identifier='1',email='client@example.com',customer_id='42',confirmed=True):
    return {'id':identifier,'customer_id':customer_id,'email':email,'active':True,'confirmed':confirmed,
            'rubrics':['1'],'created_at':'2023-01-01','updated_at':'2026-11-11','confirmed_at':'2023-01-02' if confirmed else ''}


@_legacy_auth(SALES_AUTH_REQUIRED=False)
class OverShopFullImportTests(TestCase):
    def test_all_ids_remain_separate_and_repeat_is_idempotent(self):
        rows=[customer(),customer('43')]
        result=import_snapshot(rows,[subscriber()],'one')
        self.assertEqual(result['created'],2)
        self.assertEqual(Client.objects.count(),2)
        self.assertEqual(Address.objects.count(),1)
        self.assertTrue(import_snapshot(rows,[subscriber()],'one')['repeated'])
        self.assertEqual(OverShopSubscription.objects.count(),1)
        self.assertEqual(ImportBatch.objects.count(),1)

    def test_unknown_subscription_does_not_merge_account_ownership(self):
        import_snapshot([customer()],[subscriber(customer_id='')],'one')
        self.assertIsNone(OverShopSubscription.objects.get().client_id)
        self.assertEqual(Client.objects.count(),1)
        self.assertEqual(OverShopSubscription.objects.get().address.email,'client@example.com')

    def test_anonymous_only_and_pending_preserved(self):
        result=import_snapshot([], [subscriber(customer_id='',confirmed=False)],'one')
        record=OverShopSubscription.objects.select_related('address').get()
        self.assertEqual(result['anonymous_created'],1)
        self.assertEqual(record.client.identity_key,'overshop:subscriber:1')
        self.assertEqual(subscription_state(record),'pending')
        self.assertFalse(record.confirmed)
        self.assertEqual(record.rubrics,['1'])

    def test_staff_not_imported_and_subscription_not_granted_to_client(self):
        result=import_snapshot([customer(category='staff')],[subscriber()],'one')
        self.assertEqual(result['created'],0)
        self.assertFalse(Client.objects.exists())
        record=OverShopSubscription.objects.select_related('address').get()
        self.assertEqual(subscription_state(record),'review')
        self.assertTrue(record.confirmed)
        self.assertFalse(Contact.objects.exists())

    def test_manual_data_and_suppression_survive_import(self):
        client=Client.objects.create(identity_key='overshop:user:42',name='Manual',city='Manual city')
        Address.objects.create(email='client@example.com',archived=True,suppression_reason='Отписка')
        import_snapshot([customer()],[subscriber()],'one')
        client.refresh_from_db()
        self.assertEqual(client.name,'Manual')
        self.assertEqual(client.city,'Manual city')
        record=OverShopSubscription.objects.select_related('address').get()
        self.assertEqual(subscription_state(record),'suppressed')
        self.assertEqual(record.address.suppression_reason,'Отписка')

    def test_updated_purchase_segment_replaces_old_membership(self):
        row=customer();import_snapshot([row],[],'one')
        row.update(purchase_segment='paid',orders_total=1,orders_paid=1)
        import_snapshot([row],[],'two')
        names=list(Contact.objects.get().audiences.values_list('name',flat=True))
        self.assertIn('Есть оплаченные покупки',names)
        self.assertNotIn('Не делали заказов',names)
        client=Client.objects.get()
        self.assertEqual(client.facts.filter(field='purchase_snapshot',state='accepted').count(),1)

    def test_invalid_email_is_not_a_sendable_address_and_missing_name_is_preserved(self):
        row=customer(email='broken',name='');row['issues']=['Некорректный email','Не указано имя']
        import_snapshot([row],[],'one')
        self.assertFalse(Address.objects.exists())
        self.assertIsNone(Contact.objects.get().address_id)
        self.assertEqual(Client.objects.get().name,'Клиент Over-shop №42')

    def test_duplicate_source_ids_roll_back(self):
        with self.assertRaises(ValueError):import_snapshot([customer(),customer()],[],'one')
        self.assertFalse(Client.objects.exists())

    def test_status_filters_pending_and_reports_raw_confirmation(self):
        import_snapshot([customer()],[subscriber(confirmed=False)],'one')
        r=self.client.get('/api/overshop/subscribers?state=pending').json()
        self.assertEqual(r['total'],1)
        self.assertFalse(r['rows'][0]['confirmed'])
        self.assertEqual(self.client.get('/api/overshop/subscribers?state=bad').status_code,400)
        self.assertEqual(self.client.post('/api/overshop/subscribers').status_code,405)

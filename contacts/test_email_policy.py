from django.test import override_settings as _legacy_auth
from django.test import TestCase
from django.utils import timezone
from .models import Address, Client, Contact, EmailExclusion, EmailCheckState
from .email_policy import sending_state, eligible_addresses
from .email_queue import run_round


@_legacy_auth(SALES_AUTH_REQUIRED=False)
class EmailPolicyTests(TestCase):
    def setUp(self):
        self.address=Address.objects.create(email='test@example.com', validation='Домен принимает почту', validation_result={'code':'mx'})
        self.contact=Contact.objects.create(client=Client.objects.create(name='Client',identity_key='client'), address=self.address)
        self.url=f'/api/contacts/{self.contact.pk}/email-status'

    def post(self, **data):
        return self.client.post(self.url, data=data, content_type='application/json')

    def test_multiple_restrictions_and_audit(self):
        self.assertEqual(self.post(action='exclude', reason='unsubscribe', note='Заявление').status_code,200)
        self.post(action='exclude', reason='complaint')
        self.post(action='exclude', reason='complaint')
        self.assertEqual(EmailExclusion.objects.count(),2)
        self.assertFalse(eligible_addresses(Address.objects.all()).exists())
        self.assertEqual(self.post(action='restore', reason='unsubscribe').status_code,400)
        self.post(action='restore', reason='unsubscribe',note='Новое согласие получено')
        self.address.refresh_from_db()
        self.assertEqual(sending_state(self.address)['code'],'excluded')
        self.post(action='restore', reason='complaint',note='Обращение проверено')
        self.assertTrue(eligible_addresses(Address.objects.all()).exists())
        self.assertEqual(EmailExclusion.objects.filter(removed_at__isnull=False).count(),2)

    def test_dns_failure_is_excluded_without_deleting_client(self):
        self.address.validation_result={'code':'nxdomain','reason':'DNS NXDOMAIN'};self.address.save()
        rows=self.client.get('/api/contacts/page',{'send_status':'excluded'}).json()['contacts']
        self.assertEqual([r['id'] for r in rows],[self.contact.pk])
        self.assertEqual(rows[0]['activity'],'Исключены')
        self.assertEqual(self.client.get('/api/contacts/page',{'send_status':'ready'}).json()['contacts'],[])
        self.assertEqual(Client.objects.count(),1)
        self.assertEqual(self.post(action='recheck').status_code,200)
        self.address.refresh_from_db();self.assertEqual(sending_state(self.address)['code'],'pending')
        EmailCheckState.objects.create(pk=1,enabled=True)
        run_round(lambda *args:{'code':'mx','status':'Домен принимает почту'})
        self.address.refresh_from_db();self.assertEqual(sending_state(self.address)['code'],'ready')

    def test_temporary_failure_and_archive_not_sendable(self):
        for code in ('temporary','syntax','null_mx','no_route'):
            self.address.validation_result={'code':code};self.address.save()
            self.assertFalse(eligible_addresses(Address.objects.all()).exists())
        self.address.validation_result={'code':'mx'};self.address.archived=True;self.address.save()
        self.assertFalse(eligible_addresses(Address.objects.all()).exists())

    def test_shared_email_block_and_recheck_preserves_restriction(self):
        second=Contact.objects.create(client=Client.objects.create(name='Second',identity_key='second'),address=self.address)
        self.post(action='exclude',reason='manual')
        self.post(action='recheck')
        self.assertEqual(self.client.get(f'/api/contacts/{second.pk}/email-status').json()['sending']['code'],'excluded')
        self.address.refresh_from_db();self.assertFalse(self.address.archived)

    def test_legacy_restriction_is_never_removed(self):
        self.address.suppression_reason='Ограничение источника';self.address.save()
        self.assertEqual(self.post(action='restore',reason='manual',note='test').status_code,409)
        self.address.refresh_from_db();self.assertEqual(self.address.suppression_reason,'Ограничение источника')

    def test_bad_payload_and_missing_email(self):
        self.assertEqual(self.post(action='exclude',reason='bad').status_code,400)
        self.assertEqual(self.post(action='exclude',reason=[]).status_code,400)
        self.contact.address=None;self.contact.save()
        self.assertEqual(self.client.get(self.url).status_code,400)

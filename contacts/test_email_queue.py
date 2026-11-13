from django.test import override_settings as _legacy_auth
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from .models import Address,EmailCheckState
from .email_queue import run_round


@_legacy_auth(SALES_AUTH_REQUIRED=False)
class EmailQueueTests(TestCase):
    def setUp(self):
        self.state=EmailCheckState.objects.create(pk=1,enabled=True)
        self.address=Address.objects.create(email='test@example.com')

    def test_success_and_new_contacts(self):
        check=lambda *a:{'code':'mx','status':'Домен принимает почту'}
        self.assertTrue(run_round(check))
        self.assertFalse(run_round(check))
        Address.objects.create(email='new@example.com')
        self.assertTrue(run_round(check))

    def test_temporary_retries_and_no_archiving(self):
        check=lambda *a:{'code':'temporary','status':'Временная ошибка'}
        self.assertTrue(run_round(check))
        self.address.refresh_from_db()
        self.assertFalse(self.address.archived)
        self.assertGreater(self.address.validation_next_at,timezone.now())
        self.assertFalse(run_round(check))
        Address.objects.filter(pk=self.address.pk).update(validation_next_at=timezone.now()-timedelta(seconds=1))
        self.assertTrue(run_round(check))
        self.address.refresh_from_db();self.assertEqual(self.address.validation_attempts,2)

    def test_pause_during_request_discards_result(self):
        def check(*args):
            EmailCheckState.objects.filter(pk=1).update(enabled=False)
            return {'code':'mx','status':'Домен принимает почту'}
        self.assertFalse(run_round(check))
        self.address.refresh_from_db();self.assertIsNone(self.address.validation_checked_at)

    def test_exclusions_and_lease(self):
        Address.objects.filter(pk=self.address.pk).update(suppression_reason='Отписка')
        self.assertFalse(run_round(lambda *a:self.fail('Excluded address queried')))
        EmailCheckState.objects.filter(pk=1).update(lease='another',lease_until=timezone.now()+timedelta(minutes=1))
        self.assertFalse(run_round())

    def test_api_and_worker_failure(self):
        self.assertEqual(self.client.get('/api/email-check').json()['due'],1)
        self.assertEqual(self.client.post('/api/email-check',data='{"action":"bad"}',content_type='application/json').status_code,400)
        def fail(*args):raise RuntimeError('failure')
        self.assertFalse(run_round(fail))
        self.state.refresh_from_db();self.assertFalse(self.state.enabled)

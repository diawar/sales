from django.test import override_settings as _legacy_auth
from types import SimpleNamespace
from unittest.mock import Mock
import dns.resolver
from django.test import SimpleTestCase
from .email_validation import check_email


@_legacy_auth(SALES_AUTH_REQUIRED=False)
class EmailValidationTests(SimpleTestCase):
    def test_syntax_does_not_query_dns(self):
        resolver=Mock()
        self.assertEqual(check_email('bad',resolver=resolver)['code'],'syntax')
        resolver.resolve.assert_not_called()

    def test_mx_and_domain_cache(self):
        resolver=Mock();resolver.resolve.return_value=[SimpleNamespace(exchange='mx.example.com.',preference=10)]
        cache={}
        self.assertEqual(check_email('a@example.com',cache,resolver)['code'],'mx')
        check_email('b@example.com',cache,resolver)
        self.assertEqual(resolver.resolve.call_count,1)

    def test_null_mx(self):
        resolver=Mock();resolver.resolve.return_value=[SimpleNamespace(exchange='.',preference=0)]
        self.assertEqual(check_email('a@example.com',resolver=resolver)['code'],'null_mx')

    def test_implicit_mx(self):
        resolver=Mock();resolver.resolve.side_effect=[dns.resolver.NoAnswer(),['192.0.2.1']]
        self.assertEqual(check_email('a@example.com',resolver=resolver)['code'],'implicit_mx')

    def test_no_route_and_errors(self):
        for error,expected in [(dns.resolver.NXDOMAIN(),'nxdomain'),(dns.exception.Timeout(),'temporary')]:
            resolver=Mock();resolver.resolve.side_effect=error
            self.assertEqual(check_email('a@example.com',resolver=resolver)['code'],expected)
        resolver=Mock();resolver.resolve.side_effect=dns.resolver.NoAnswer()
        self.assertEqual(check_email('a@example.com',resolver=resolver)['code'],'no_route')

from django.test import override_settings as _legacy_auth
import json
import smtplib
import uuid
from unittest.mock import patch
from django.core import mail
from django.test import TestCase,Client,override_settings
from .models import TemplateTestMail,TemplateTestMailGate


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',SALES_SMTP_ENABLED=True,
    EMAIL_HOST='smtp.example.com',EMAIL_PORT=587,EMAIL_USE_TLS=True,EMAIL_USE_SSL=False,
    EMAIL_HOST_USER='test',EMAIL_HOST_PASSWORD='secret-not-for-browser',DEFAULT_FROM_EMAIL='sender@example.com',
    SALES_FROM_NAME='OVER',SALES_TEST_RECIPIENTS=['tester@example.com'])
@_legacy_auth(SALES_AUTH_REQUIRED=False)
class TemplateMailTests(TestCase):
    def payload(self):
        return {'token':str(uuid.uuid4()),'recipient':'tester@example.com','subject':'Здравствуйте, {{name|клиент}}',
                'preheader':'Предложение','html':'<html><head><meta name="viewport"></head><body><p>Привет, {{name|клиент}}</p><a href="{{unsubscribe_url}}">Отписаться</a></body></html>',
                'values':{'name':'Алексей <script>x</script>'}}

    def post(self,data):return self.client.post('/api/templates/test-mail',json.dumps(data),content_type='application/json')

    def test_mime_personalization_and_idempotency(self):
        data=self.payload();r=self.post(data)
        self.assertEqual(r.status_code,200);self.assertEqual(r.json()['state'],'accepted')
        self.assertEqual(len(mail.outbox),1);message=mail.outbox[0]
        self.assertEqual(message.to,['tester@example.com']);self.assertTrue(message.subject.startswith('[ТЕСТ]'))
        html=message.alternatives[0].content
        self.assertIn('&lt;script&gt;',html);self.assertNotIn('href="https://example.com/unsubscribe-preview"',html)
        self.assertIn('ТЕСТОВОЕ ПИСЬМО',message.body);self.assertIn('в тесте отключена',html)
        self.assertEqual(self.post(data).json()['state'],'accepted');self.assertEqual(len(mail.outbox),1)
        data['subject']='Другая тема';self.assertEqual(self.post(data).status_code,409)

    @override_settings(SALES_SMTP_ENABLED=False)
    @patch('contacts.template_mail.EmailMultiAlternatives.send')
    def test_unconfigured_never_sends(self,send):
        r=self.client.get('/api/templates/test-mail');self.assertFalse(r.json()['configured'])
        self.assertNotIn('secret-not-for-browser',r.content.decode());self.assertEqual(self.post(self.payload()).status_code,503)
        send.assert_not_called();self.assertEqual(TemplateTestMail.objects.count(),0)

    @patch('contacts.template_mail.EmailMultiAlternatives.send')
    def test_allowlist_validation_and_html_errors(self,send):
        data=self.payload();data['recipient']='other@example.com';self.assertEqual(self.post(data).status_code,403)
        data=self.payload();data['html']='<script>x</script>';self.assertEqual(self.post(data).status_code,400)
        data=self.payload();data['subject']='Hi\nBcc: x@example.com';self.assertEqual(self.post(data).status_code,400)
        for value in [[],{},dict(self.payload(),token='bad'),dict(self.payload(),values={'name':[]})]:self.assertEqual(self.post(value).status_code,400)
        send.assert_not_called()

    @patch('contacts.template_mail.EmailMultiAlternatives.send',side_effect=smtplib.SMTPAuthenticationError(535,b'secret-not-for-browser'))
    def test_auth_error_not_leaked_or_retried(self,send):
        data=self.payload();r=self.post(data);self.assertEqual(r.json()['state'],'failed')
        self.assertNotIn('secret-not-for-browser',r.content.decode());self.post(data);self.assertEqual(send.call_count,1)

    @patch('contacts.template_mail.EmailMultiAlternatives.send',side_effect=TimeoutError('unknown'))
    def test_uncertain_send_not_retried(self,send):
        data=self.payload();self.assertEqual(self.post(data).json()['state'],'unknown')
        self.assertEqual(self.post(data).json()['state'],'unknown');self.assertEqual(send.call_count,1)

    def test_rate_limit_and_history(self):
        self.post(self.payload());self.assertEqual(self.post(self.payload()).status_code,429)
        r=self.client.get('/api/templates/test-mail').json()
        self.assertEqual(len(r['history']),1);self.assertEqual(r['history'][0]['state'],'accepted')
        self.assertNotIn('fingerprint',r['history'][0])

    def test_csrf_and_readonly_get(self):
        c=Client(enforce_csrf_checks=True)
        self.assertEqual(c.post('/api/templates/test-mail','{}',content_type='application/json').status_code,403)
        with patch('contacts.template_mail.EmailMultiAlternatives.send') as send:self.client.get('/api/templates/test-mail');send.assert_not_called()

    @override_settings(EMAIL_USE_TLS=True,EMAIL_USE_SSL=True)
    def test_ambiguous_tls_blocked(self):
        self.assertFalse(self.client.get('/api/templates/test-mail').json()['configured'])

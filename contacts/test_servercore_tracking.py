from django.test import override_settings as _legacy_auth
import json
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlsplit
import httpx
from bs4 import BeautifulSoup
from django.test import TestCase, Client, RequestFactory, override_settings
from django.utils import timezone
from .models import (EmailTemplate, TrackedMessage, TrackingLink, TrackingEvent,
                     EmailSubscription, SubscriptionToken, Address, EmailExclusion, CampaignTestEvent)
from . import servercore


@override_settings(SALES_PUBLIC_BASE_URL='https://sales.example.com')
@_legacy_auth(SALES_AUTH_REQUIRED=False)
class TrackingTests(TestCase):
    def setUp(self):
        self.template=EmailTemplate.objects.create(name='Offer',subject='Hello {{name}}',preheader='News',
            html='<html><body><p>{{name}}: {{email}}</p><a href="https://shop.example.com/offers?a=1&amp;b=2">Offer</a><a href="https://shop.example.com/offers?a=1&amp;b=2">Again</a><a href="mailto:info@example.com">Mail</a><a href="{{ unsubscribe_url }}">Unsubscribe</a></body></html>')
        self.payload={'template_id':self.template.pk,'recipient':'Person@Example.com','values':{'name':'Name <b>X</b>'}}

    def preview(self,**changes):
        return self.client.post('/api/mail-tracking/preview',self.payload|changes,content_type='application/json')

    def path(self,url):return urlsplit(url).path

    def test_preparation_instruments_html_without_sending_or_forging_consent(self):
        with patch('django.core.mail.message.EmailMessage.send') as send,patch('httpx.Client.request') as network:
            r=self.preview()
        self.assertEqual(r.status_code,201);send.assert_not_called();network.assert_not_called()
        data=r.json();self.assertFalse(data['sent']);self.assertTrue(data['is_test'])
        self.assertIn('Name &lt;b&gt;X&lt;/b&gt;',data['html'])
        soup=BeautifulSoup(data['html'],'html.parser')
        self.assertEqual(len(data['clicks']),1)
        self.assertEqual(soup.find_all('a')[0]['href'],soup.find_all('a')[1]['href'])
        self.assertEqual(soup.find_all('a')[2]['href'],'mailto:info@example.com')
        self.assertEqual(soup.find_all('a')[3]['href'],data['unsubscribe_url'])
        self.assertEqual(TrackingEvent.objects.count(),0)
        self.assertEqual(CampaignTestEvent.objects.count(),0)
        self.assertEqual(Address.objects.count(),0)
        row=EmailSubscription.objects.get();self.assertTrue(row.is_test);self.assertEqual(row.state,'pending')
        self.assertIsNone(row.confirmed_at)
        self.assertNotIn('person@example.com',data['pixel_url']);self.assertNotIn('@',data['pixel_url'])
        raw=self.path(data['pixel_url']).rsplit('/',1)[-1].removesuffix('.gif')
        self.assertFalse(TrackingLink.objects.filter(token_hash=raw).exists())
        self.assertEqual(r['Cache-Control'],'no-store, no-cache, max-age=0')

    def test_pixel_click_and_head_are_idempotent_and_do_not_claim_delivery(self):
        data=self.preview().json();pixel=self.path(data['pixel_url']);click=self.path(data['clicks'][0]['url'])
        self.client.head(pixel);self.client.head(click)
        self.assertEqual(TrackingEvent.objects.count(),0)
        for _ in range(2):
            image=self.client.get(pixel);redirect=self.client.get(click+'?url=https://evil.example.com')
            self.assertEqual(image['Content-Type'],'image/gif');self.assertTrue(image.content.startswith(b'GIF89a'))
            self.assertEqual(redirect.status_code,302)
            self.assertEqual(redirect['Location'],'https://shop.example.com/offers?a=1&b=2')
            self.assertEqual(redirect['Referrer-Policy'],'no-referrer')
        self.assertEqual(TrackingEvent.objects.count(),2)
        stats=self.client.get('/api/mail-tracking').json()
        self.assertEqual(stats['totals'],{'open':1,'click':1,'unsubscribe':0})
        self.assertEqual(stats['messages'][0]['state'],'preview')
        self.assertFalse('delivered' in stats['totals'])

    def test_expired_unknown_or_wrong_purpose_links_do_not_record(self):
        data=self.preview().json();pixel=self.path(data['pixel_url'])
        unknown=self.client.get('/track/o/'+'x'*43+'.gif')
        self.assertEqual(unknown.status_code,200)
        self.assertEqual(self.client.get('/track/c/'+'x'*43+'/').status_code,404)
        TrackedMessage.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.client.get(pixel);self.assertEqual(self.client.get(self.path(data['clicks'][0]['url'])).status_code,404)
        self.assertEqual(TrackingEvent.objects.count(),0)

    def test_unsubscribe_get_is_safe_post_requires_csrf_and_changes_only_test_subscription(self):
        address=Address.objects.create(email='person@example.com')
        real=EmailSubscription.objects.create(email=address.email,is_test=False,state='confirmed',source='Imported',
            consent_text='Yes',consent_version='v1',confirmed_at=timezone.now())
        data=self.preview().json();url=self.path(data['unsubscribe_url']);browser=Client(enforce_csrf_checks=True)
        response=browser.get(url);self.assertEqual(TrackingEvent.objects.count(),0)
        self.assertEqual(browser.post(url,{}).status_code,403)
        for _ in range(2):
            response=browser.post(url,{'csrfmiddlewaretoken':browser.cookies['sales_csrftoken'].value})
            self.assertEqual(response.status_code,200)
        self.assertEqual(TrackingEvent.objects.filter(kind='unsubscribe').count(),1)
        real.refresh_from_db();address.refresh_from_db()
        self.assertEqual(real.state,'confirmed');self.assertEqual(address.suppression_reason,'')
        self.assertEqual(EmailExclusion.objects.count(),0)
        self.assertEqual(EmailSubscription.objects.get(is_test=True).state,'revoked')

    def test_real_unsubscribe_integrates_with_exclusions(self):
        data=self.preview().json();m=TrackedMessage.objects.get()
        # Simulate a future accepted message only inside the test database.
        sub=m.unsubscribe_token.subscription;sub.is_test=False;sub.save()
        m.is_test=False;m.state='accepted';m.save()
        self.client.post(self.path(data['unsubscribe_url']))
        self.assertTrue(EmailExclusion.objects.filter(address__email='person@example.com',reason='unsubscribe',removed_at=None).exists())
        self.assertEqual(self.client.get('/api/mail-tracking?mode=real').json()['totals']['unsubscribe'],1)
        self.assertEqual(self.client.get('/api/mail-tracking?mode=test').json()['totals']['unsubscribe'],0)

    def test_two_messages_have_separate_links_and_unique_recipient_counts(self):
        first=self.preview().json();second=self.preview().json()
        self.assertNotEqual(first['pixel_url'],second['pixel_url'])
        for row in (first,second):self.client.get(self.path(row['pixel_url']))
        r=self.client.get('/api/mail-tracking?kind=open&query=PERSON&direction=asc').json()
        self.assertEqual(r['totals']['open'],2);self.assertEqual(r['unique']['open'],1)
        r=self.client.get('/api/mail-tracking',{'message_id':first['id']}).json()
        self.assertEqual(r['totals']['open'],1)
        self.client.post(self.path(first['unsubscribe_url']))
        self.client.post(self.path(second['unsubscribe_url']))
        self.assertEqual(TrackingEvent.objects.filter(kind='unsubscribe').count(),2)

    def test_rejects_real_send_invalid_input_and_unsafe_targets_atomically(self):
        for changes in ({'is_test':False},{'send':True},{'recipient':'bad'},{'values':{'name':[]}}):
            self.assertEqual(self.preview(**changes).status_code,400)
        self.template.html='<p>Offer</p><a href="https://user:password@shop.example.com/">Link</a><a href="{{unsubscribe_url}}">Unsubscribe</a>'
        self.template.save()
        self.assertEqual(self.preview().status_code,400)
        self.assertEqual(TrackedMessage.objects.count(),0);self.assertEqual(SubscriptionToken.objects.count(),0)

    @override_settings(SALES_PUBLIC_BASE_URL='')
    def test_public_links_require_configured_origin_outside_localhost(self):
        with override_settings(ALLOWED_HOSTS=['sales.example.com','testserver']):
            self.assertEqual(self.client.post('/api/mail-tracking/preview',self.payload,content_type='application/json',HTTP_HOST='sales.example.com').status_code,400)
        self.assertEqual(self.preview().status_code,201)

    def test_filter_validation_and_csrf_on_preview(self):
        for query in ('mode=all','direction=x','kind=sent','message_id=bad','page=0'):
            self.assertEqual(self.client.get('/api/mail-tracking?'+query).status_code,400)
        browser=Client(enforce_csrf_checks=True)
        self.assertEqual(browser.post('/api/mail-tracking/preview',self.payload,content_type='application/json').status_code,403)


RESOURCE='448355c9-8c3a-4706-8ca4-ed2970fce631'


@override_settings(SERVERCORE_RESOURCE_ID=RESOURCE,SERVERCORE_IAM_TOKEN='secret-api-token',
    SERVERCORE_SENDER_DOMAIN='example.com',EMAIL_HOST='smtp.servercore-smtp.com',EMAIL_PORT=1126,
    EMAIL_USE_TLS=True,EMAIL_USE_SSL=False,EMAIL_HOST_USER='secret-user',EMAIL_HOST_PASSWORD='secret-password',
    DEFAULT_FROM_EMAIL='sender@example.com',SALES_MAIL_PROVIDER='servercore',SALES_SMTP_ENABLED=True)
@_legacy_auth(SALES_AUTH_REQUIRED=False)
class ServercoreTests(TestCase):
    def response(self,data,status=200,headers=None):
        return httpx.Response(status,json=data,headers=headers,request=httpx.Request('GET',servercore.API_BASE))

    def test_config_is_offline_and_secrets_are_not_exposed(self):
        with patch('httpx.Client.request') as network:
            response=self.client.get('/api/servercore')
        network.assert_not_called();payload=response.json()
        self.assertFalse(payload['can_send']);self.assertTrue(payload['api_configured']);self.assertTrue(payload['smtp_configured'])
        self.assertNotIn('secret',response.content.decode())
        self.assertEqual(self.client.post('/api/servercore',{'action':'send'},content_type='application/json').status_code,400)

    @patch('httpx.Client.request')
    def test_inspect_only_reads_and_filters_resource_secrets(self,network):
        network.side_effect=[self.response({'resource':{'id':RESOURCE,'name':'Mail','status':'active','password':'must-not-leak','login':'must-not-leak'}}),
                             self.response({'verification':{'dkim_status':True,'spf_status':False}})]
        r=self.client.post('/api/servercore',{'action':'inspect'},content_type='application/json')
        self.assertEqual(r.status_code,200);self.assertNotIn('must-not-leak',r.content.decode())
        self.assertFalse(r.json()['sent']);self.assertTrue(r.json()['verification']['dkim_status'])
        self.assertEqual(network.call_count,2)
        for call in network.call_args_list:
            self.assertEqual(call.args[0],'GET');self.assertTrue(call.args[1].startswith(servercore.API_BASE+'/resources/'+RESOURCE))
            self.assertEqual(call.kwargs['headers']['X-Auth-Token'],'secret-api-token')

    @patch('httpx.Client.request')
    def test_provider_errors_are_redacted_and_not_retried(self,network):
        for status in (401,403,429,500,302):
            network.return_value=self.response({'error':'secret-api-token'},status)
            r=self.client.post('/api/servercore',{'action':'inspect'},content_type='application/json')
            self.assertEqual(r.status_code,400);self.assertNotIn('secret-api-token',r.content.decode())

    def test_smtp_is_blocked_even_when_environment_enables_it(self):
        from .template_mail import configuration
        with patch('smtplib.SMTP') as smtp:
            self.assertFalse(configuration()['configured'])
            with self.assertRaises(RuntimeError):servercore.ReviewOnlyBackend().send_messages([object()])
        smtp.assert_not_called()

    @override_settings(SERVERCORE_IAM_TOKEN='',SERVERCORE_ACCOUNT_ID='account',SERVERCORE_PROJECT_NAME='project',
        SERVERCORE_SERVICE_USER='service',SERVERCORE_SERVICE_PASSWORD='password')
    @patch('httpx.Client.request')
    def test_service_user_token_is_project_scoped_cached_and_refreshed(self,network):
        servercore._cached=None
        expiry=(timezone.now()+timedelta(hours=24)).isoformat()
        network.return_value=self.response({'token':{'expires_at':expiry}},201,{'X-Subject-Token':'new-token'})
        self.assertEqual(servercore.iam_token(),'new-token');self.assertEqual(servercore.iam_token(),'new-token')
        self.assertEqual(network.call_count,1)
        call=network.call_args;self.assertEqual(call.args,('POST',servercore.IDENTITY))
        self.assertEqual(call.kwargs['json']['auth']['scope']['project']['name'],'project')
        servercore._cached=(servercore._cached[0],'old',timezone.now()-timedelta(seconds=1))
        self.assertEqual(servercore.iam_token(),'new-token');self.assertEqual(network.call_count,2)
        servercore._cached=None

    @override_settings(SERVERCORE_IAM_TOKEN='',SERVERCORE_ACCOUNT_ID='',SERVERCORE_PROJECT_NAME='',SERVERCORE_SERVICE_USER='',SERVERCORE_SERVICE_PASSWORD='')
    @patch('httpx.Client.request')
    def test_unconfigured_inspection_does_not_touch_network(self,network):
        self.assertEqual(self.client.post('/api/servercore',{'action':'inspect'},content_type='application/json').status_code,400)
        network.assert_not_called()

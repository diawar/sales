from django.test import override_settings as _legacy_auth
from datetime import timedelta
from urllib.parse import urlsplit
from unittest.mock import patch
from django.test import TestCase,Client as HttpClient,override_settings
from django.utils import timezone
from .models import EmailSubscription,SubscriptionToken,SubscriptionEvent,Address,EmailExclusion,Client,Contact,Audience,CampaignTestEvent
from .subscriptions import request_subscription,unsubscribe_link,apply,lookup


@_legacy_auth(SALES_AUTH_REQUIRED=False)
class SubscriptionTests(TestCase):
    def request_record(self,test=True,**values):
        return request_subscription({'email':'person@example.com','source':'Test source','is_test':test,**values})

    def test_request_requires_confirmation_and_stores_source_history_hash_only(self):
        row,raw,token=self.request_record()
        self.assertEqual(row.state,'pending');self.assertIsNone(row.confirmed_at)
        self.assertNotEqual(token.token_hash,raw);self.assertEqual(len(token.token_hash),64)
        self.assertEqual(lookup(raw).pk,token.pk)
        self.assertEqual(row.history.get().snapshot['source'],'Test source')
        self.assertIn(row.consent_text,row.history.get().snapshot['consent_text'])
        self.assertEqual(Address.objects.count(),0)

    def test_get_never_confirms_and_checkbox_is_required(self):
        row,raw,token=self.request_record()
        self.assertEqual(self.client.get(f'/subscription/{raw}/').status_code,200)
        row.refresh_from_db();self.assertEqual(row.state,'pending')
        self.assertEqual(self.client.post(f'/subscription/{raw}/',{}).status_code,400)
        self.assertEqual(self.client.post(f'/subscription/{raw}/',{'consent':'yes'}).status_code,200)
        row.refresh_from_db();self.assertEqual(row.state,'confirmed')
        self.assertEqual(self.client.post(f'/subscription/{raw}/',{'consent':'yes'}).status_code,200)
        self.assertEqual(row.history.filter(kind='confirmed').count(),1)

    def test_public_post_has_csrf_and_privacy_headers(self):
        row,raw,_=self.request_record();url=f'/subscription/{raw}/'
        browser=HttpClient(enforce_csrf_checks=True)
        response=browser.get(url)
        self.assertEqual(response['Cache-Control'],'no-store')
        self.assertEqual(response['Referrer-Policy'],'no-referrer')
        self.assertNotContains(response,row.email)
        self.assertEqual(browser.post(url,{'consent':'yes'}).status_code,403)
        response=browser.post(url,{'consent':'yes','csrfmiddlewaretoken':browser.cookies['sales_csrftoken'].value})
        self.assertEqual(response.status_code,200)
        row.refresh_from_db();self.assertEqual(row.state,'confirmed')

    def test_test_confirm_and_unsubscribe_never_touch_real_base(self):
        address=Address.objects.create(email='person@example.com',suppression_reason='Жалоба')
        EmailExclusion.objects.create(address=address,reason='complaint')
        row,raw,_=self.request_record();apply(raw,True)
        _,unsub,_=unsubscribe_link(row.pk);apply(unsub)
        row.refresh_from_db();address.refresh_from_db()
        self.assertEqual(row.state,'revoked');self.assertEqual(address.suppression_reason,'Жалоба')
        self.assertEqual(EmailExclusion.objects.count(),1)
        self.assertEqual(CampaignTestEvent.objects.count(),0)

    def test_real_unsubscribe_blocks_all_case_variants_and_is_idempotent(self):
        Address.objects.create(email='person@example.com',validation_result={'code':'mx'})
        Address.objects.create(email='PERSON@EXAMPLE.COM',validation_result={'code':'mx'})
        row,raw,_=self.request_record(False);apply(raw,True)
        _,unsub,_=unsubscribe_link(row.pk)
        self.client.get(f'/subscription/{unsub}/')
        self.assertEqual(Address.objects.exclude(suppression_reason='').count(),0)
        apply(unsub);apply(unsub)
        self.assertEqual(Address.objects.filter(suppression_reason='Отписка').count(),2)
        self.assertEqual(row.history.filter(kind='revoked').count(),1)
        self.assertEqual(EmailExclusion.objects.filter(reason='unsubscribe',removed_at=None).count(),2)

    def test_new_confirmation_removes_only_optout(self):
        address=Address.objects.create(email='person@example.com',suppression_reason='Жалоба')
        EmailExclusion.objects.create(address=address,reason='complaint')
        row,raw,_=self.request_record(False);apply(raw,True)
        _,unsub,_=unsubscribe_link(row.pk);apply(unsub)
        row,new,_=self.request_record(False,source='New request');address.refresh_from_db()
        self.assertIn('Отписка',address.suppression_reason)
        apply(new,True);address.refresh_from_db();row.refresh_from_db()
        self.assertEqual(row.state,'confirmed');self.assertEqual(address.suppression_reason,'Жалоба')
        self.assertEqual(EmailExclusion.objects.filter(reason='unsubscribe',removed_at=None).count(),0)
        self.assertEqual(row.history.filter(kind='confirmed').count(),2)
        self.assertEqual(row.history.filter(kind='revoked').count(),1)

    def test_unsubscribe_invalidates_pending_confirmations(self):
        row,raw,_=self.request_record(False)
        _,unsub,_=unsubscribe_link(row.pk);apply(unsub)
        self.assertEqual(self.client.post(f'/subscription/{raw}/',{'consent':'yes'}).status_code,410)
        row.refresh_from_db();self.assertEqual(row.state,'revoked')

    def test_new_request_invalidates_previous_pending_link(self):
        row,old,_=self.request_record()
        row,new,_=self.request_record(source='New source')
        self.assertEqual(self.client.get(f'/subscription/{old}/').status_code,410)
        apply(new,True)
        self.assertEqual(list(row.history.filter(kind='requested').order_by('pk').values_list('source',flat=True)),['Test source','New source'])

    def test_used_confirm_link_cannot_restore_after_unsubscribe(self):
        row,raw,_=self.request_record(False);apply(raw,True)
        _,unsub,_=unsubscribe_link(row.pk);apply(unsub)
        apply(raw,True)
        row.refresh_from_db();self.assertEqual(row.state,'revoked')

    def test_old_unsubscribe_link_still_works_after_explicit_resubscribe(self):
        row,raw,_=self.request_record(False);apply(raw,True)
        _,unsub,_=unsubscribe_link(row.pk);apply(unsub)
        row,new,_=self.request_record(False);apply(new,True);apply(unsub)
        row.refresh_from_db();self.assertEqual(row.state,'revoked')
        self.assertEqual(row.history.filter(kind='revoked').count(),2)

    def test_expiry_and_invalid_tokens(self):
        row,raw,token=self.request_record()
        token.expires_at=timezone.now()-timedelta(seconds=1);token.save()
        self.assertEqual(self.client.get(f'/subscription/{raw}/').status_code,410)
        self.assertEqual(self.client.get('/subscription/not-a-token/').status_code,404)
        row.refresh_from_db();self.assertEqual(row.state,'pending')

    def test_legacy_restrictions_are_preserved(self):
        address=Address.objects.create(email='person@example.com',suppression_reason='Ограничение источника')
        row,raw,_=self.request_record(False);apply(raw,True)
        _,unsub,_=unsubscribe_link(row.pk);apply(unsub)
        row,new,_=self.request_record(False);apply(new,True)
        address.refresh_from_db();self.assertEqual(address.suppression_reason,'Ограничение источника')

    def test_manual_restore_cannot_bypass_centre_revocation(self):
        row,raw,_=self.request_record(False);apply(raw,True)
        _,unsub,_=unsubscribe_link(row.pk);apply(unsub)
        address=Address.objects.get(email=row.email)
        contact=Contact.objects.create(client=Client.objects.create(name='Client',identity_key='client'),address=address)
        response=self.client.post(f'/api/contacts/{contact.pk}/email-status',data={'action':'restore','reason':'unsubscribe','note':'Override'},content_type='application/json')
        self.assertEqual(response.status_code,409)
        address.refresh_from_db();self.assertIn('Отписка',address.suppression_reason)

    @override_settings(SALES_PUBLIC_BASE_URL='https://sales.example.kz')
    def test_api_list_filters_link_history_and_mode_separation(self):
        payload={'email':'PERSON@EXAMPLE.COM','source':'Website','is_test':True}
        result=self.client.post('/api/subscriptions',data=payload,content_type='application/json')
        self.assertEqual(result.status_code,201)
        data=result.json();self.assertTrue(data['url'].startswith('https://sales.example.kz/subscription/'))
        raw=urlsplit(data['url']).path.split('/')[-2]
        listing=self.client.get('/api/subscriptions?query=person&mode=test').json()
        self.assertEqual(listing['pagination']['total'],1);self.assertNotIn(raw,str(listing))
        detail=self.client.get('/api/subscriptions/'+str(data['subscription']['id'])).json()
        self.assertEqual(detail['history'][0]['kind'],'requested')
        self.assertNotIn('token_hash',str(detail))
        self.request_record(False)
        self.assertEqual(self.client.get('/api/subscriptions?mode=real').json()['pagination']['total'],1)
        self.assertEqual(self.client.get('/api/subscriptions?mode=test').json()['pagination']['total'],1)

    def test_invalid_requests(self):
        for payload in [{'email':'bad','source':'Source'},{'email':'a@example.com','source':''},{'email':'a@example.com','source':'Source','is_test':'false'},
                        {'email':'a@example.com','source':'Source','source_url':'javascript:alert(1)'},
                        {'email':'a@example.com','source':'Source','source_at':'bad'},
                        {'email':'a@example.com','source':'Source','source_at':(timezone.now()+timedelta(days=1)).isoformat()}]:
            self.assertEqual(self.client.post('/api/subscriptions',data=payload,content_type='application/json').status_code,400)
        self.assertEqual(EmailSubscription.objects.count(),0)
        self.assertEqual(self.client.get('/api/subscriptions?mode=unknown').status_code,400)
        self.assertEqual(self.client.get('/api/subscriptions/99999').status_code,404)

    @override_settings(SALES_SMTP_ENABLED=True)
    def test_no_transport_and_readiness_uses_only_real_confirmations(self):
        with patch('django.core.mail.EmailMultiAlternatives.send',side_effect=AssertionError('Mail used')),patch('smtplib.SMTP',side_effect=AssertionError('SMTP used')),patch('socket.create_connection',side_effect=AssertionError('Network used')):
            row,raw,_=self.request_record();apply(raw,True)
            _,unsub,_=unsubscribe_link(row.pk);apply(unsub)
        self.assertEqual(Address.objects.count(),0)

    def test_public_ajax_keeps_csrf_and_rejects_null_origin(self):
        row,raw,_=self.request_record();url=f'/subscription/{raw}/'
        browser=HttpClient(enforce_csrf_checks=True);browser.get(url)
        csrf=browser.cookies['sales_csrftoken'].value
        response=browser.post(url,{'consent':'yes','csrfmiddlewaretoken':csrf},HTTP_ACCEPT='application/json',HTTP_X_CSRFTOKEN=csrf,HTTP_ORIGIN='null')
        self.assertEqual(response.status_code,403)
        row.refresh_from_db();self.assertEqual(row.state,'pending')
        response=browser.post(url,{'consent':'yes','csrfmiddlewaretoken':csrf},HTTP_ACCEPT='application/json',HTTP_X_CSRFTOKEN=csrf,HTTP_ORIGIN='http://testserver')
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['state'],'confirmed')
        row.refresh_from_db();self.assertEqual(row.state,'confirmed')

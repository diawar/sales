import hashlib
import io
import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.management import call_command,CommandError
from django.test import TestCase,Client,override_settings
from django.utils import timezone
from .models import AccountAccessGate,ActionAuditEntry,EmailTemplate,EmailSubscription,Address,EmailExclusion,CampaignSender,EmailCampaign,ActivityTestEvent,TestFrequencyPolicy
from .subscriptions import issue
from .mail_tracking import prepare_preview
from django.test import RequestFactory

User=get_user_model()
PASSWORD='Orchid-Paper-Cobalt-482!'
HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher']


@override_settings(SALES_AUTH_REQUIRED=True,PASSWORD_HASHERS=HASHERS)
class AccountTests(TestCase):
    def setUp(self):
        self.owner=User.objects.create_superuser('owner',password=PASSWORD)
        self.member=User.objects.create_user('member',password=PASSWORD)
    def signin(self,client=None,name='owner',password=PASSWORD,**extra):
        return (client or self.client).post('/login/',{'username':name,'password':password,**extra})

    def test_all_private_apis_and_assets_require_login(self):
        for path in ('/api/contacts','/api/accounts','/api/action-journal','/api/campaigns','/api/rehearsals',
            '/api/test-frequency','/api/automations','/api/templates','/api/subscriptions','/api/contacts/export'):
            response=self.client.get(path);self.assertEqual(response.status_code,401,path)
            self.assertEqual(response['Content-Type'],'application/json');self.assertIn('no-store',response['Cache-Control'])
        self.assertEqual(self.client.get('/').status_code,302)
        self.assertEqual(self.client.get('/database.js').status_code,302)
        self.assertEqual(self.client.get('/auth.css').status_code,200)
        self.assertEqual(self.client.get('/api/auth/session').json()['authenticated'],False)

    def test_login_session_audit_author_and_logout(self):
        self.assertEqual(self.signin().status_code,302)
        state=self.client.get('/api/auth/session').json();self.assertTrue(state['authenticated']);self.assertEqual(state['user']['username'],'owner')
        self.assertNotIn('password',state['user']);self.assertEqual(self.client.get('/api/contacts/summary').status_code,200)
        event=ActionAuditEntry.objects.get(action='signed_in');self.assertEqual(event.actor_key,'user:'+str(self.owner.pk));self.assertEqual(event.actor_source,'authenticated')
        self.assertTrue(self.client.cookies['sales_sessionid']['httponly']);self.assertEqual(self.client.cookies['sales_sessionid']['samesite'],'Strict')
        self.assertEqual(self.client.get('/logout/').status_code,405)
        self.assertEqual(self.client.post('/logout/').status_code,302)
        self.assertEqual(ActionAuditEntry.objects.get(action='signed_out').actor_key,event.actor_key)
        self.assertEqual(self.client.get('/api/campaigns').status_code,401)

    def test_wrong_disabled_unknown_credentials_are_generic(self):
        self.member.is_active=False;self.member.save()
        a=self.signin(name='missing',password='Wrong_098').content
        b=self.signin(name='member').content
        self.assertIn('Не удалось войти'.encode(),a);self.assertIn('Не удалось войти'.encode(),b)
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
        self.assertEqual(ActionAuditEntry.objects.filter(action='login_failed').count(),2)

    def test_password_throttle_preserves_admin_role_controls(self):
        self.signin()
        for _ in range(5):
            response=self.client.post('/accounts/',{'action':'password','old_password':'wrong','new_password1':PASSWORD,'new_password2':PASSWORD})
            self.assertEqual(response.status_code,400)
        response=self.client.post('/accounts/',{'action':'password'})
        self.assertEqual(response.status_code,429);self.assertEqual(response['Retry-After'],'900')
        self.assertTrue(response.context['administrator']);self.assertTrue(response.context['roles'])
        self.assertContains(response,'value="viewer" selected',status_code=429)

    def test_login_rate_limit_and_window(self):
        for _ in range(5):self.assertEqual(self.signin(password='wrong').status_code,400)
        response=self.signin();self.assertEqual(response.status_code,429);self.assertEqual(response['Retry-After'],'900')
        with patch('contacts.accounts.timezone.now',return_value=timezone.now()+timedelta(minutes=16)):
            self.assertEqual(self.signin().status_code,302)

    def test_login_csrf_and_no_external_next_redirect(self):
        self.assertEqual(self.client.get('/login/')['Referrer-Policy'],'same-origin')
        c=Client(enforce_csrf_checks=True)
        self.assertEqual(c.post('/login/',{'username':'owner','password':PASSWORD}).status_code,403)
        self.assertEqual(self.signin(next='https://attacker.invalid/').url,'/')
        self.client.post('/logout/')
        self.assertEqual(self.signin(next='//attacker.invalid/').url,'/')
        self.client.post('/logout/')
        self.assertEqual(self.signin(next='/accounts/').url,'/accounts/')

    def test_cookie_names_are_isolated_from_other_local_apps(self):
        c=Client(enforce_csrf_checks=True);c.get('/login/')
        own=c.cookies['sales_csrftoken'].value
        c.cookies['csrftoken']='0'*32;c.cookies['sessionid']='other-application-session'
        response=c.post('/login/',{'username':'owner','password':PASSWORD,'csrfmiddlewaretoken':own})
        self.assertEqual(response.status_code,302)
        self.assertTrue(c.get('/api/auth/session').json()['authenticated'])

    def test_csrf_failure_is_friendly_and_does_not_apply_credentials(self):
        c=Client(enforce_csrf_checks=True);c.get('/login/')
        response=c.post('/login/',{'username':'owner','password':PASSWORD,'csrfmiddlewaretoken':'0'*32})
        self.assertEqual(response.status_code,403);self.assertIn('Открыть форму заново'.encode(),response.content)
        self.assertFalse(c.get('/api/auth/session').json()['authenticated'])
        entry=ActionAuditEntry.objects.get(action='login_failed');self.assertEqual(entry.after['csrf_reason'],'token_mismatch')

    def test_case_insensitive_login_does_not_allow_passwordless_access(self):
        self.assertEqual(self.signin(name='OWNER').status_code,302)
        self.client.post('/logout/')
        self.assertEqual(self.signin(name='OWNER',password='wrong').status_code,400)

    def test_regular_user_cannot_manage_accounts_or_inject_admin_flags(self):
        self.signin(name='member')
        self.assertEqual(self.client.get('/api/accounts').status_code,403)
        response=self.client.post('/accounts/',{'action':'create','username':'bad','password1':PASSWORD,'password2':PASSWORD,'is_superuser':'1'})
        self.assertEqual(response.status_code,403);self.assertFalse(User.objects.filter(username='bad').exists())
        response=self.client.post('/accounts/',{'action':'active','user_id':self.owner.pk,'active':'0'})
        self.assertEqual(response.status_code,403);self.owner.refresh_from_db();self.assertTrue(self.owner.is_active)

    def test_admin_creates_only_regular_account_hashes_and_logs(self):
        self.signin()
        response=self.client.post('/accounts/',{'action':'create','username':'new-person','first_name':'User',
            'password1':PASSWORD,'password2':PASSWORD,'is_superuser':'1','is_staff':'1'})
        self.assertEqual(response.status_code,200)
        user=User.objects.get(username='new-person');self.assertFalse(user.is_superuser);self.assertFalse(user.is_staff)
        self.assertNotEqual(user.password,PASSWORD);self.assertTrue(user.check_password(PASSWORD))
        event=ActionAuditEntry.objects.get(action='account_created');self.assertEqual(event.actor_key,'user:'+str(self.owner.pk))
        content=json.dumps(list(ActionAuditEntry.objects.values()),default=str)
        self.assertNotIn(PASSWORD,content);self.assertNotIn(user.password,content)
        response=self.client.post('/accounts/',{'action':'create','username':'NEW-PERSON','password1':PASSWORD,'password2':PASSWORD})
        self.assertEqual(response.status_code,400);self.assertEqual(User.objects.filter(username__iexact='new-person').count(),1)

    def test_password_validation_and_confirmation(self):
        self.signin()
        for p1,p2 in [('123','123'),(PASSWORD,'different')]:
            response=self.client.post('/accounts/',{'action':'create','username':'invalid','password1':p1,'password2':p2})
            self.assertEqual(response.status_code,400);self.assertFalse(User.objects.filter(username='invalid').exists())
        long='Very-Long-Password!'+('x'*1024)
        self.assertEqual(self.client.post('/accounts/',{'action':'create','username':'too-long','password1':long,'password2':long}).status_code,400)
        self.assertFalse(User.objects.filter(username='too-long').exists())
        self.assertEqual(self.client.post('/accounts/',{'action':'password','old_password':PASSWORD,'new_password1':long,'new_password2':long}).status_code,400)
        self.owner.refresh_from_db();self.assertTrue(self.owner.check_password(PASSWORD))

    def test_deactivation_ends_existing_access_and_self_disable_is_blocked(self):
        other=Client();self.signin(other,name='member');self.signin()
        self.assertEqual(other.get('/api/campaigns').status_code,200)
        self.client.post('/accounts/',{'action':'active','user_id':self.member.pk,'active':'0'})
        self.assertEqual(other.get('/api/campaigns').status_code,401)
        self.client.post('/accounts/',{'action':'active','user_id':self.member.pk,'active':'1'})
        self.assertEqual(other.get('/api/campaigns').status_code,401)
        response=self.client.post('/accounts/',{'action':'active','user_id':self.owner.pk,'active':'0'})
        self.assertEqual(response.status_code,400);self.owner.refresh_from_db();self.assertTrue(self.owner.is_active)

    def test_own_password_change_requires_old_and_invalidates_other_sessions(self):
        self.signin();other=Client();self.signin(other)
        new='Juniper-Sky-River-903!'
        response=self.client.post('/accounts/',{'action':'password','old_password':'wrong','new_password1':new,'new_password2':new})
        self.assertEqual(response.status_code,400)
        response=self.client.post('/accounts/',{'action':'password','old_password':PASSWORD,'new_password1':new,'new_password2':new})
        self.assertEqual(response.status_code,200);self.assertEqual(self.client.get('/api/campaigns').status_code,200)
        self.assertEqual(other.get('/api/campaigns').status_code,401)
        event=ActionAuditEntry.objects.get(action='password_changed',result='success');self.assertNotIn(new,json.dumps(event.after))

    def test_declared_operator_and_spoofed_headers_cannot_replace_logged_user(self):
        with override_settings(SALES_AUTH_REQUIRED=False):self.client.post('/api/action-journal/operator',{'name':'Fake director'},content_type='application/json')
        self.signin()
        self.assertEqual(self.client.post('/api/action-journal/operator',{'name':'Fake'},content_type='application/json').status_code,409)
        self.client.post('/api/test-frequency',{'mode':'simulation','test_only':True,'revision':0,
            'policy':{'enabled':True,'min_interval_minutes':60,'max_24h':2,'max_7d':3}},content_type='application/json',HTTP_X_USER='Director',HTTP_REMOTE_USER='Director')
        row=ActionAuditEntry.objects.filter(object_type='frequency').get()
        self.assertEqual(row.actor_label,'owner');self.assertEqual(row.actor_source,'authenticated')
        self.assertEqual(self.client.get('/api/action-journal').json()['current_actor']['key'],'user:'+str(self.owner.pk))

    def test_public_tracking_and_unsubscribe_still_work_without_login(self):
        template=EmailTemplate.objects.create(name='Test',subject='News',preheader='',html='<a href="https://example.com/offer">Offer</a><a href="{{unsubscribe_url}}">Unsubscribe</a>')
        with override_settings(SALES_PUBLIC_BASE_URL='http://127.0.0.1:8173'):
            p=prepare_preview(RequestFactory().get('/'),template,'public@example.invalid',{})
        from urllib.parse import urlsplit
        self.assertEqual(self.client.get(urlsplit(p['pixel_url']).path).status_code,200)
        self.assertEqual(self.client.get(urlsplit(p['clicks'][0]['url']).path).status_code,302)
        path=urlsplit(p['unsubscribe_url']).path
        self.assertEqual(self.client.get(path).status_code,200);self.assertEqual(self.client.post(path).status_code,200)
        self.assertEqual(self.client.get('/subscription-form.js').status_code,200)

    def test_accounts_flow_no_smtp_network_or_contact_writes(self):
        Address.objects.create(email='unchanged@example.invalid')
        before=list(Address.objects.values())
        with patch('smtplib.SMTP',side_effect=AssertionError('SMTP')),patch('httpx.Client.send',side_effect=AssertionError('HTTP')),patch('socket.create_connection',side_effect=AssertionError('network')):
            self.signin();self.client.get('/accounts/');self.client.post('/logout/')
        self.assertEqual(list(Address.objects.values()),before);self.assertFalse(EmailExclusion.objects.exists())


@override_settings(SALES_AUTH_REQUIRED=True,PASSWORD_HASHERS=HASHERS)
class InitialAccountTests(TestCase):
    def prepare(self,folder):
        output=io.StringIO()
        with override_settings(DATA_DIR=Path(folder)):call_command('prepare_auth_setup',stdout=output)
        raw=(Path(folder)/'private/account-setup-code.txt').read_text().strip()
        self.assertNotIn(raw,output.getvalue());return raw

    def setup_data(self,code,**changes):
        return {'setup_code':code,'username':'initial-owner','first_name':'Owner','password1':PASSWORD,'password2':PASSWORD}|changes

    def test_setup_requires_private_one_time_code_and_creates_first_owner(self):
        self.assertTrue(self.client.get('/api/auth/session').json()['setup_required'])
        self.assertEqual(self.client.get('/').status_code,302)
        with TemporaryDirectory() as folder:
            code=self.prepare(folder)
            self.assertEqual(AccountAccessGate.objects.get().setup_digest,hashlib.sha256(code.encode()).hexdigest())
            self.assertEqual(self.client.post('/setup/',self.setup_data('wrong')).status_code,400)
            self.assertFalse(User.objects.exists())
            response=self.client.post('/setup/',self.setup_data(code));self.assertEqual(response.status_code,302)
            user=User.objects.get();self.assertTrue(user.is_superuser);self.assertTrue(user.check_password(PASSWORD))
            self.assertEqual(AccountAccessGate.objects.get().setup_digest,'')
            self.assertEqual(self.client.get('/api/action-journal').json()['current_actor']['key'],'user:'+str(user.pk))
            self.assertEqual(self.client.post('/setup/',self.setup_data(code,username='second')).status_code,302)
            self.assertEqual(User.objects.count(),1)
            with override_settings(DATA_DIR=Path(folder)):
                with self.assertRaises(CommandError):call_command('prepare_auth_setup',stdout=io.StringIO())

    def test_expired_code_weak_password_and_csrf_do_not_create_owner(self):
        with TemporaryDirectory() as folder:
            code=self.prepare(folder)
            self.assertEqual(Client(enforce_csrf_checks=True).post('/setup/',self.setup_data(code)).status_code,403)
            self.assertEqual(self.client.post('/setup/',self.setup_data(code,password1='123',password2='123')).status_code,400)
            self.assertFalse(User.objects.exists())
            AccountAccessGate.objects.update(setup_expires_at=timezone.now()-timedelta(seconds=1))
            self.assertEqual(self.client.post('/setup/',self.setup_data(code)).status_code,400)
            self.assertFalse(User.objects.exists())

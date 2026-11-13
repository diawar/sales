from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase,Client,override_settings
from .models import AccountRole,ActionAuditEntry,Address
from .access import allowed,role,assigned_role

User=get_user_model()

@override_settings(SALES_AUTH_REQUIRED=True,PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class RoleTests(TestCase):
    def setUp(self):
        self.admin=User.objects.create_superuser('owner',password='Fixture-only-123!')
        self.users={}
        for code in ('manager','editor','viewer'):
            u=User.objects.create_user(code,password='Fixture-only-123!')
            AccountRole.objects.create(user=u,role=code);self.users[code]=u
        self.client.force_login(self.admin)

    def test_matrix_rejects_unknown_routes_and_privileged_connections(self):
        for u in self.users.values():
            for path in ('/api/unknown-future-api','/api/accounts','/api/servercore','/api/gov','/api/overshop/preview','/api/kaspi','/api/journal'):
                self.assertFalse(allowed(u,path,'GET'),path)
                self.assertFalse(allowed(u,path,'POST'),path)
        self.assertTrue(allowed(self.admin,'/api/accounts','GET'))

    def test_manager_and_editor_scope(self):
        manager,editor,viewer=[self.users[r] for r in ('manager','editor','viewer')]
        for path in ('/api/templates','/api/templates/1','/api/templates/check'):
            self.assertTrue(allowed(editor,path,'POST'));self.assertFalse(allowed(viewer,path,'POST'))
        for path in ('/api/import/commit','/api/campaigns','/api/campaigns/1/queue','/api/subscriptions/1','/api/contacts/1/profile'):
            self.assertTrue(allowed(manager,path,'POST'),path);self.assertFalse(allowed(editor,path,'POST'),path)
        self.assertFalse(allowed(editor,'/api/contacts/export','GET'))
        self.assertTrue(allowed(manager,'/api/contacts/export','GET'))

    def test_direct_requests_denied_and_attributed_without_contact_mutation(self):
        Address.objects.create(email='fixture@example.invalid');before=list(Address.objects.values())
        self.client.force_login(self.users['viewer'])
        for path in ('/api/import/commit','/api/campaigns','/api/templates','/api/servercore','/api/contacts/1/profile'):
            response=self.client.post(path,{'role':'admin'},content_type='application/json')
            self.assertEqual(response.status_code,403,path);self.assertTrue(response.json()['permission_denied'])
        self.assertEqual(list(Address.objects.values()),before)
        self.assertTrue(ActionAuditEntry.objects.filter(result='rejected',actor_key='user:'+str(self.users['viewer'].pk)).exists())

    def test_role_change_immediate_for_existing_session_and_audited(self):
        target=self.users['manager'];c=Client();c.force_login(target)
        self.assertTrue(c.get('/api/auth/session').json()['user']['permissions']['operations'])
        response=self.client.post('/accounts/',{'action':'role','user_id':target.pk,'role':'viewer'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(c.post('/api/campaigns',{},content_type='application/json').status_code,403)
        self.assertFalse(c.get('/api/auth/session').json()['user']['permissions']['operations'])
        event=ActionAuditEntry.objects.get(action='account_changed')
        self.assertEqual(event.before,{'role':'manager'});self.assertEqual(event.after,{'role':'viewer'})
        self.assertEqual(event.actor_key,'user:'+str(self.admin.pk))

    def test_no_self_escalation_or_owner_demotion(self):
        for action in ('role','create','active'):
            self.client.force_login(self.users['manager'])
            self.assertEqual(self.client.post('/accounts/',{'action':action,'user_id':self.users['manager'].pk,'role':'admin'}).status_code,403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post('/accounts/',{'action':'role','user_id':self.admin.pk,'role':'viewer'}).status_code,400)
        self.assertEqual(self.client.post('/accounts/',{'action':'role','user_id':self.users['viewer'].pk,'role':'invalid'}).status_code,400)

    def test_assigned_administrator_can_manage_accounts_without_superuser_flag(self):
        target=self.users['viewer']
        self.assertEqual(self.client.post('/accounts/',{'action':'role','user_id':target.pk,'role':'admin'}).status_code,200)
        self.client.force_login(target)
        self.assertEqual(self.client.get('/api/accounts').status_code,200)
        self.assertContains(self.client.get('/accounts/'),'Пользователи')
        target.refresh_from_db();self.assertFalse(target.is_superuser)

    def test_missing_role_defaults_to_viewer_and_private_api_is_still_gated(self):
        u=User.objects.create_user('legacy');self.assertEqual(role(u),'viewer')
        self.assertFalse(allowed(u,'/api/campaigns','POST'))
        self.client.logout();self.assertEqual(self.client.get('/api/campaigns').status_code,401)

    def test_disabled_user_retains_assigned_role_and_no_privileges(self):
        target=self.users['editor'];target.is_active=False;target.save()
        self.assertEqual(assigned_role(target),'editor');self.assertEqual(role(target),'')
        self.assertFalse(allowed(target,'/api/templates','POST'))
        self.assertFalse(allowed(target,'/api/contacts','GET'))
        response=self.client.get('/accounts/')
        self.assertContains(response,'value="editor" selected')
        data=next(u for u in self.client.get('/api/accounts').json()['users'] if u['id']==target.pk)
        self.assertEqual(data['role'],'editor');self.assertFalse(data['permissions']['templates'])
        self.assertEqual(self.client.post('/accounts/',{'action':'active','user_id':target.pk,'active':'1'}).status_code,200)
        target=User.objects.get(pk=target.pk);self.assertEqual(role(target),'editor');self.assertFalse(target.is_superuser)

    def test_no_role_can_send_even_if_smtp_configured(self):
        with override_settings(SALES_SMTP_ENABLED=True),patch('smtplib.SMTP',side_effect=AssertionError('network')):
            for u in [self.admin,*self.users.values()]:
                self.client.force_login(u)
                self.assertEqual(self.client.post('/api/templates/test-mail',{},content_type='application/json').status_code,403)
                self.assertFalse(self.client.get('/api/auth/session').json()['user']['permissions']['send'])

    def test_authenticated_read_sections_work_for_every_role(self):
        paths=('/api/contacts/page','/api/contacts/summary','/api/audiences','/api/templates',
            '/api/campaigns','/api/campaign-senders','/api/automations','/api/rehearsals','/api/action-journal',
            '/api/notifications','/api/auth/session')
        for user in (self.admin,*self.users.values()):
            self.client.force_login(user)
            for path in paths:
                response=self.client.get(path)
                self.assertEqual(response.status_code,200,(user.username,path,response.content[:100]))
                self.assertIn('no-store',response.get('Cache-Control',''),path)

    def test_client_supplied_role_and_headers_cannot_escalate(self):
        self.client.force_login(self.users['viewer'])
        response=self.client.post('/api/templates?role=admin',{'role':'admin','is_superuser':True},content_type='application/json',HTTP_X_ROLE='admin',HTTP_X_USER='owner')
        self.assertEqual(response.status_code,403)
        self.assertEqual(self.client.get('/api/auth/session').json()['user']['role'],'viewer')

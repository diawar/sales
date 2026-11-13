from django.test import override_settings as _legacy_auth
import json
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase, Client, RequestFactory, override_settings
from django.utils import timezone
from . import action_audit as audit, test_campaigns as campaigns_fixture, test_rehearsal as rehearsal_fixture
from . import automation_simulation as engine, rehearsal
from .models import (ActionAuditEntry, EmailCampaign, Address, EmailExclusion, EmailSubscription,
    AutomationScenario, CampaignQueue, ActivityTestEvent)


@_legacy_auth(SALES_AUTH_REQUIRED=False)
class ActionAuditTests(TestCase):
    add_contact=campaigns_fixture.CampaignTests.add_contact
    def setUp(self):campaigns_fixture.CampaignTests.setUp(self)

    def post(self,url,data):return self.client.post(url,data,content_type='application/json')

    def campaign(self):
        response=self.post('/api/campaigns',self.data)
        self.assertEqual(response.status_code,201,response.content)
        return response.json()

    def test_create_update_actor_and_immutable_snapshots(self):
        self.post('/api/action-journal/operator',{'name':'Оператор А'})
        row=self.campaign();created=ActionAuditEntry.objects.get(action='created',object_type='campaign')
        self.assertEqual(created.actor_label,'Оператор А');self.assertEqual(created.actor_source,'declared')
        self.assertEqual(created.before,{});self.assertEqual(created.after['revision'],1)
        self.post('/api/campaigns/'+str(row['id']),self.data|{'name':'Changed','revision':1})
        changed=ActionAuditEntry.objects.get(action='updated',object_type='campaign',result='success')
        self.assertEqual(changed.before['name'],'Campaign');self.assertEqual(changed.after['name'],'Changed')
        self.assertEqual(changed.before['revision'],1);self.assertEqual(changed.after['revision'],2)
        created.refresh_from_db();self.assertEqual(created.after['name'],'Campaign')
        self.assertEqual(changed.actor_key,created.actor_key);self.assertNotEqual(changed.request_id,created.request_id)
        self.assertEqual(list(changed.campaign_refs.values_list('campaign_id',flat=True)),[row['id']])

    def test_anonymous_browser_stability_and_two_operators(self):
        a=self.campaign();self.post('/api/campaigns/'+str(a['id']),self.data|{'name':'First','revision':1})
        events=list(ActionAuditEntry.objects.filter(object_type='campaign').order_by('pk'))
        self.assertEqual(events[0].actor_key,events[1].actor_key);self.assertEqual(events[0].actor_source,'anonymous')
        other=Client();other.post('/api/action-journal/operator',{'name':'Operator B'},content_type='application/json')
        other.post('/api/campaigns',self.data|{'name':'Second'},content_type='application/json')
        latest=ActionAuditEntry.objects.filter(object_type='campaign').latest('pk')
        self.assertEqual(latest.actor_label,'Operator B');self.assertNotEqual(latest.actor_key,events[0].actor_key)

    def test_payload_headers_and_forged_cookie_cannot_supply_verified_actor(self):
        self.client.cookies[audit.COOKIE]='forged'
        response=self.client.post('/api/campaigns',self.data|{'actor':'Director','user':'Director','password':'NOT_LOGGED','html':'SECRET_HTML'},
            content_type='application/json',HTTP_X_USER='Director',HTTP_REMOTE_USER='Director')
        self.assertEqual(response.status_code,201)
        row=ActionAuditEntry.objects.get(action='created');self.assertEqual(row.actor_source,'anonymous')
        self.assertNotEqual(row.actor_label,'Director')
        saved=json.dumps({'before':row.before,'after':row.after,'summary':row.summary})
        self.assertNotIn('NOT_LOGGED',saved);self.assertNotIn('SECRET_HTML',saved)
        self.assertNotIn('good@example.com',saved);self.assertNotIn('unsubscribe_url',saved)

    def test_authenticated_and_server_identity_override_declared_name(self):
        request=RequestFactory().post('/api/campaigns')
        request.user=SimpleNamespace(is_authenticated=True,pk=42,get_username=lambda:'verified-user')
        marker=audit.context.set({'request':request,'identity':{'id':'browser','name':'fake'},'request_id':None})
        try:
            self.assertEqual(audit.actor(),{'key':'user:42','label':'verified-user','source':'authenticated'})
            del request.user;request.META['REMOTE_USER']='server-user'
            self.assertEqual(audit.actor()['source'],'remote_user');self.assertEqual(audit.actor()['label'],'server-user')
        finally:audit.context.reset(marker)
        self.assertEqual(audit.actor()['source'],'system')

    def test_failed_version_attempt_logged_without_successful_mutation(self):
        row=self.campaign();before=ActionAuditEntry.objects.count()
        response=self.post('/api/campaigns/'+str(row['id']),self.data|{'revision':99,'name':'Must not save'})
        self.assertEqual(response.status_code,409);self.assertEqual(ActionAuditEntry.objects.count(),before+1)
        rejected=ActionAuditEntry.objects.latest('pk');self.assertEqual(rejected.result,'rejected')
        self.assertEqual(rejected.after,{});self.assertEqual(EmailCampaign.objects.get(pk=row['id']).name,'Campaign')

    def test_success_and_log_roll_back_together_if_audit_write_fails(self):
        original=audit.record
        def failing(*a,**kw):
            if kw.get('result')=='rejected':return original(*a,**kw)
            raise ValueError('Audit storage unavailable')
        with patch('contacts.action_audit.record',side_effect=failing):response=self.post('/api/campaigns',self.data)
        self.assertEqual(response.status_code,400);self.assertFalse(EmailCampaign.objects.exists())
        self.assertFalse(ActionAuditEntry.objects.filter(result='success').exists())
        self.assertEqual(ActionAuditEntry.objects.get().result,'rejected')

    def test_queue_prepare_start_pause_resume_cancel_and_idempotency(self):
        row=self.campaign();url=f'/api/campaigns/{row["id"]}/queue'
        self.assertEqual(self.post(url,{'action':'prepare','campaign_revision':1}).status_code,200)
        self.post(url,{'action':'prepare','campaign_revision':1})
        for action in ('resume','pause','pause','resume','cancel','cancel'):self.post(url,{'action':action})
        self.assertEqual(list(ActionAuditEntry.objects.filter(object_type='queue').order_by('pk').values_list('action',flat=True)),
            ['prepared','started','paused','resumed','cancelled'])
        self.assertEqual(CampaignQueue.objects.get().state,'cancelled')

    def test_filters_dates_pagination_details_and_read_only_api(self):
        row=self.campaign();actor=ActionAuditEntry.objects.get().actor_key
        other=self.campaign();before=ActionAuditEntry.objects.count()
        response=self.client.get('/api/action-journal',{'campaign_id':row['id'],'actor':actor,'action':'created','object_type':'campaign','result':'success','direction':'asc'})
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['pagination']['total'],1)
        entry=response.json()['events'][0];self.assertEqual(entry['object_id'],row['id']);self.assertEqual(entry['after']['revision'],1)
        self.assertEqual(ActionAuditEntry.objects.count(),before);self.assertIn('no-store',response['Cache-Control'])
        self.assertEqual(self.client.post('/api/action-journal',{},content_type='application/json').status_code,405)
        self.assertEqual(self.client.delete('/api/action-journal').status_code,405)
        for params in ({'page':'bad'},{'action':'bad'},{'from':'bad'},{'from':'2026-12-01','to':'2026-01-01'},{'campaign_id':'0'}):
            self.assertEqual(self.client.get('/api/action-journal',params).status_code,400)
        self.assertEqual(ActionAuditEntry.objects.count(),before)

    def test_operator_label_validation_and_csrf_rejection_is_distinct(self):
        self.assertEqual(self.post('/api/action-journal/operator',{'name':'\nspoof'}).status_code,400)
        c=Client(enforce_csrf_checks=True)
        response=c.post('/api/campaigns',self.data,content_type='application/json')
        self.assertEqual(response.status_code,403)
        event=ActionAuditEntry.objects.get();self.assertEqual(event.result,'rejected');self.assertEqual(event.action,'created')
        self.assertEqual(event.before,{});self.assertFalse(EmailCampaign.objects.exists())

    def test_renaming_operator_does_not_rewrite_old_author(self):
        self.post('/api/action-journal/operator',{'name':'Before name'})
        self.campaign();created=ActionAuditEntry.objects.get(action='created')
        self.post('/api/action-journal/operator',{'name':'After name'})
        created.refresh_from_db();self.assertEqual(created.actor_label,'Before name')
        response=self.client.get('/api/action-journal',{'actor':created.actor_key})
        self.assertEqual(response.json()['current_actor']['label'],'After name')
        self.assertEqual(response.json()['pagination']['total'],3)

    def test_date_inclusive_filter_and_pagination(self):
        with audit.transaction.atomic():
            for i in range(27):audit.record('created','scenario',i+1,'Test '+str(i))
        today=timezone.localdate().isoformat()
        response=self.client.get('/api/action-journal',{'from':today,'to':today,'page':'2','direction':'asc'})
        data=response.json();self.assertEqual(data['pagination']['total'],27);self.assertEqual(len(data['events']),2)
        self.assertEqual([e['object_id'] for e in data['events']],[26,27])


@override_settings(SALES_PUBLIC_BASE_URL='https://sales.example.com')
@_legacy_auth(SALES_AUTH_REQUIRED=False)
class ScenarioAuditTests(TestCase):
    setUp=rehearsal_fixture.RehearsalTests.setUp
    event=rehearsal_fixture.RehearsalTests.event
    start=rehearsal_fixture.RehearsalTests.start
    act=rehearsal_fixture.RehearsalTests.act

    def post(self,url,data):return self.client.post(url,data,content_type='application/json')

    def create(self):
        response=self.post('/api/automations',{'mode':'simulation','test_only':True,'name':'Audit scenario','config':self.base})
        self.assertEqual(response.status_code,201,response.content);return response.json()

    def test_scenario_edits_start_pause_resume_cancel_and_polling(self):
        self.post('/api/action-journal/operator',{'name':'Scenario operator'})
        row=self.create();sid=row['id']
        self.post(f'/api/automations/{sid}',{'mode':'simulation','test_only':True,'name':'Renamed','revision':1,'config':self.base})
        changed=ActionAuditEntry.objects.get(object_type='scenario',action='updated')
        self.assertEqual((changed.before['revision'],changed.after['revision']),(1,2))
        response=self.post(f'/api/automations/{sid}/run',{'mode':'simulation','test_only':True,'revision':2})
        run=response.json()['run_id']
        for action in ('pause','resume','cancel'):self.post('/api/automation-simulation',{'mode':'simulation','test_only':True,'action':action,'run_id':run})
        entries=ActionAuditEntry.objects.filter(object_type='simulation').order_by('pk')
        self.assertEqual(list(entries.values_list('action',flat=True)),['started','paused','resumed','cancelled'])
        self.assertTrue(all(e.actor_label=='Scenario operator' for e in entries))
        paused=entries.get(action='paused');self.assertEqual(paused.before,{'state':'running'});self.assertEqual(paused.after['state'],'paused')
        before=ActionAuditEntry.objects.count()
        self.post('/api/automation-simulation',{'mode':'simulation','test_only':True,'action':'tick'})
        self.client.get('/api/automations');self.client.get('/api/action-journal')
        self.assertEqual(ActionAuditEntry.objects.count(),before)

    def test_rehearsal_lifecycle_and_auto_stop_actor_initiator_correlation(self):
        self.post('/api/action-journal/operator',{'name':'Review operator'})
        scenario=self.create()
        response=self.post('/api/rehearsals',{'mode':'simulation','test_only':True,'scenario_id':scenario['id'],'revision':1,
            'values':{'name':'Name','company':'Company','city':'City'},'safety_policy':{'enabled':True,'min_sample':1,'errors_percent':'100','permanent_percent':'100','complaints_percent':'0'}})
        rid=response.json()['id']
        self.post(f'/api/rehearsals/{rid}',{'mode':'simulation','test_only':True,'action':'advance','minutes':10})
        from .models import CampaignRehearsal
        row=CampaignRehearsal.objects.get(pk=rid);letter=row.letters.get()
        import uuid
        self.post(f'/api/rehearsals/{rid}',{'mode':'simulation','test_only':True,'action':'delivery_event','kind':'complaint',
            'letter_id':letter.pk,'attempt':1,'event_id':str(uuid.uuid4()),'detail':'PRIVATE RECIPIENT DETAILS'})
        stopped=ActionAuditEntry.objects.get(action='auto_stopped')
        self.assertEqual(stopped.actor_source,'system');self.assertEqual(stopped.initiated_by['label'],'Review operator')
        triggered=ActionAuditEntry.objects.get(action='test_event');self.assertEqual(stopped.request_id,triggered.request_id)
        self.assertEqual(stopped.after['state'],'safety_stopped');self.assertIn('порог',stopped.summary)
        self.assertEqual(stopped.before,{'state':'running'})
        self.assertNotIn('PRIVATE RECIPIENT DETAILS',json.dumps(triggered.after))
        self.post(f'/api/rehearsals/{rid}',{'mode':'simulation','test_only':True,'action':'tick'})
        self.assertEqual(ActionAuditEntry.objects.filter(action='auto_stopped').count(),1)

    def test_service_calls_are_system_and_production_base_untouched(self):
        before=(list(Address.objects.values()),list(EmailSubscription.objects.filter(is_test=False).values()),list(ActivityTestEvent.objects.values()))
        with patch('smtplib.SMTP',side_effect=AssertionError('SMTP')),patch('httpx.Client.send',side_effect=AssertionError('HTTP')),patch('socket.create_connection',side_effect=AssertionError('network')):
            row=self.start();self.act(row,'pause');self.act(row,'resume');self.act(row,'cancel')
        self.assertTrue(all(e.actor_source=='system' for e in ActionAuditEntry.objects.all()))
        self.assertEqual(before,(list(Address.objects.values()),list(EmailSubscription.objects.filter(is_test=False).values()),list(ActivityTestEvent.objects.values())))
        self.assertFalse(EmailExclusion.objects.exists())

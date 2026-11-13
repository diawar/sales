from django.test import override_settings as _legacy_auth
import uuid
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlsplit
from django.test import TestCase, RequestFactory, Client as HttpClient, override_settings
from django.utils import timezone
from . import rehearsal, automation_simulation as engine
from .models import (EmailTemplate, CampaignSender, EmailCampaign, ActivityTestEvent, CampaignRehearsal,
    TrackedMessage, TrackingEvent, Address, EmailSubscription, EmailExclusion, SubscriptionEvent,
    AutomationSimulationAction, AutomationSimulationClock, CampaignTestEvent, RehearsalObservation)
from .subscriptions import issue


@override_settings(SALES_PUBLIC_BASE_URL='https://sales.example.com')
@_legacy_auth(SALES_AUTH_REQUIRED=False)
class RehearsalTests(TestCase):
    def setUp(self):
        self.now=timezone.now()
        from .models import TestFrequencyPolicy
        TestFrequencyPolicy.objects.create(pk=1,enabled=False,clock_at=self.now)
        self.request=RequestFactory().get('/')
        self.template=EmailTemplate.objects.create(name='Letter',subject='Hello {{name}}',preheader='News',
            html='<p>{{name}} / {{company}} / {{city}}</p><a href="https://shop.example.com/offer">Offer</a><a href="{{unsubscribe_url}}">Unsubscribe</a>')
        sender=CampaignSender.objects.create(name='OVER',email='sender@example.invalid')
        self.campaign=EmailCampaign.objects.create(name='Campaign',template=self.template,sender=sender,
            template_snapshot={k:getattr(self.template,k) for k in ('id','name','subject','preheader','html','revision')},
            sender_snapshot={k:getattr(sender,k) for k in ('name','email','reply_to')})
        for person in ('active','passive','reader'):
            for i,days in enumerate((30,20,10)):self.event(person,'delivered',days,str(i))
        self.event('active','click',1);self.event('reader','open',1)
        self.base={'target':'active','steps':[{'campaign_id':self.campaign.pk,'delay_minutes':0},
            {'campaign_id':self.campaign.pk,'delay_minutes':60}],'min_interval_minutes':60,'max_24h':2,'max_7d':3}

    def event(self,email,kind,days=0,message=''):
        return ActivityTestEvent.objects.create(event_id=uuid.uuid4(),recipient=email+'@example.invalid',kind=kind,
            message_key=message,occurred_at=self.now-timedelta(days=days))

    def start(self,values=None,**config):
        scenario=engine.save({'mode':'simulation','test_only':True,'name':'End to end','config':self.base|config})
        return rehearsal.start(self.request,{'mode':'simulation','test_only':True,'scenario_id':scenario.pk,
            'revision':scenario.revision,'values':values if values is not None else {'name':'Name <b>X</b>','company':'OVER','city':'Almaty'}})

    def act(self,row,action='tick',**data):
        return rehearsal.act(self.request,row.pk,{'mode':'simulation','test_only':True,'action':action,**data})

    def test_prepares_personalized_frozen_letter_without_delivery(self):
        row=self.start();letter=row.letters.get();message=letter.message
        self.assertEqual(message.campaign,self.campaign);self.assertTrue(message.is_test)
        self.assertEqual(message.subject,'Hello Name <b>X</b>');self.assertIn('Name &lt;b&gt;X&lt;/b&gt;',letter.preview_html)
        self.assertNotIn('/track/',letter.preview_html);self.assertNotIn('/subscription/',letter.preview_html)
        self.assertFalse(TrackingEvent.objects.exists());self.assertTrue(letter.checks['can_prepare'])
        self.assertFalse(letter.checks['can_send']);self.assertEqual(message.preparation_snapshot['rehearsal_id'],row.pk)
        self.campaign.template_snapshot['subject']='CHANGED';self.campaign.save();self.template.subject='NEW';self.template.save()
        row=self.act(row,'advance',minutes=60)
        self.assertEqual(row.letters.count(),2);self.assertEqual(row.letters.last().message.subject,message.subject)
        self.assertEqual(row.members[0]['state'],'completed')

    def test_click_changes_passive_audience_and_stops_next_step(self):
        row=self.start(target='passive',opens='none');self.assertEqual(row.letters.count(),1)
        letter=row.letters.get();row=self.act(row,'event',letter_id=letter.pk,kind='click')
        self.assertEqual(row.members[0]['state'],'exited');self.assertEqual(rehearsal.people(row)[letter.recipient]['state'],'active')
        self.act(row,'advance',minutes=1440);self.assertEqual(row.letters.count(),1)

    def test_open_alone_stays_passive_but_none_condition_exits(self):
        row=self.start(target='passive',opens='none');letter=row.letters.get()
        row=self.act(row,'event',letter_id=letter.pk,kind='open')
        self.assertEqual(row.members[0]['state'],'exited');self.assertEqual(rehearsal.people(row)[letter.recipient]['state'],'passive')
        second=self.start(target='passive');letter=second.letters.filter(recipient='passive@example.invalid').get()
        second=self.act(second,'event',letter_id=letter.pk,kind='open')
        self.assertEqual(next(m for m in second.members if m['email']==letter.recipient)['state'],'waiting')

    def test_unsubscribe_isolated_from_real_and_other_rehearsals(self):
        real=EmailSubscription.objects.create(email='active@example.invalid',is_test=False,state='confirmed')
        address=Address.objects.create(email=real.email)
        first=self.start();second=self.start();letter=first.letters.get()
        first=self.act(first,'event',letter_id=letter.pk,kind='unsubscribe')
        self.assertEqual(first.members[0]['state'],'excluded');self.assertEqual(first.letters.count(),1)
        second=self.act(second,'advance',minutes=60);self.assertEqual(second.letters.count(),2)
        real.refresh_from_db();address.refresh_from_db();self.assertEqual(real.state,'confirmed');self.assertFalse(address.suppression_reason)
        self.assertFalse(EmailExclusion.objects.exists());self.assertEqual(SubscriptionEvent.objects.filter(kind='revoked').count(),1)

    def test_repeated_events_are_idempotent_and_virtual_time_is_fixed(self):
        row=self.start();letter=row.letters.get()
        for kind in ('open','click','unsubscribe'):
            for _ in range(2):self.act(row,'event',letter_id=letter.pk,kind=kind)
        self.assertEqual(TrackingEvent.objects.count(),3);self.assertEqual(RehearsalObservation.objects.count(),3)
        self.assertEqual(row.logs.filter(kind__in=['open','click','unsubscribe']).count(),3)
        self.assertEqual(set(row.observations.values_list('virtual_at',flat=True)),{row.clock_at})

    def test_public_tracking_and_unsubscribe_feed_same_rehearsal(self):
        captured=[]
        original=rehearsal.prepare_preview
        def save(*a,**kw):
            result=original(*a,**kw);captured.append(result);return result
        with patch('contacts.rehearsal.prepare_preview',side_effect=save):row=self.start(target='passive',opens='none')
        payload=captured[0]
        path=urlsplit(payload['pixel_url']).path
        self.client.head(path);self.assertEqual(row.observations.count(),0)
        self.client.get(path);self.client.get(path);self.assertEqual(row.observations.count(),1)
        row=self.act(row);self.assertEqual(row.members[0]['state'],'exited')
        self.assertEqual(self.client.get(urlsplit(payload['clicks'][0]['url']).path).status_code,302)
        self.assertEqual(rehearsal.people(row)['passive@example.invalid']['state'],'active')
        path=urlsplit(payload['unsubscribe_url']).path
        self.client.get(path);self.assertFalse(TrackingEvent.objects.filter(kind='unsubscribe').exists())
        response=self.client.post(path);self.assertEqual(response.status_code,200)
        self.assertTrue(row.observations.filter(event__kind='unsubscribe').exists())

    def test_limits_pause_resume_cancel_and_no_catchup(self):
        row=self.start(max_24h=1)
        row=self.act(row,'advance',minutes=60);self.assertEqual(row.letters.count(),1)
        self.assertEqual(row.members[0]['next_at'],(row.clock_at+timedelta(hours=23)).isoformat())
        self.act(row,'pause')
        with self.assertRaises(ValueError):self.act(row,'advance',minutes=60)
        row=self.act(row,'resume');row=self.act(row,'advance',minutes=1380);self.assertEqual(row.letters.count(),2)
        self.act(row);self.assertEqual(row.letters.count(),2)
        other=self.start(steps=[{'campaign_id':self.campaign.pk,'delay_minutes':100}])
        other=self.act(other,'cancel');self.assertEqual(other.members[0]['state'],'cancelled')
        with self.assertRaises(ValueError):self.act(other,'resume')
        self.assertEqual(other.letters.count(),0)

    def test_checks_block_missing_personalization_and_invalid_sender(self):
        row=self.start(values={});self.assertEqual(row.members[0]['state'],'blocked');self.assertEqual(row.letters.count(),0)
        self.campaign.sender_snapshot['email']='bad';self.campaign.save()
        row=self.start();self.assertEqual(row.members[0]['state'],'blocked');self.assertIn('отправитель',row.members[0]['detail'])

    def test_new_real_exclusion_checked_immediately_before_next_step(self):
        row=self.start();address=Address.objects.create(email='active@example.invalid')
        EmailExclusion.objects.create(address=address,reason='complaint')
        row=self.act(row,'advance',minutes=60)
        self.assertEqual(row.members[0]['state'],'excluded');self.assertEqual(row.letters.count(),1)

    def test_source_is_frozen_and_does_not_receive_events(self):
        row=self.start(target='passive',opens='none');before=list(ActivityTestEvent.objects.values())
        self.event('passive','click')
        row=self.act(row);self.assertEqual(row.members[0]['state'],'waiting')
        self.act(row,'event',letter_id=row.letters.get().pk,kind='click')
        self.assertEqual(ActivityTestEvent.objects.count(),len(before)+1)
        self.assertFalse(CampaignTestEvent.objects.exists());self.assertFalse(AutomationSimulationAction.objects.exists())

    def test_no_network_mail_or_real_mutations(self):
        with patch('socket.create_connection',side_effect=AssertionError('network')),patch('smtplib.SMTP',side_effect=AssertionError('SMTP')),patch('httpx.Client.send',side_effect=AssertionError('HTTP')),patch('django.core.mail.send_mail',side_effect=AssertionError('mail')):
            row=self.start();clock=AutomationSimulationClock.objects.get().clock_at
            self.act(row,'event',letter_id=row.letters.get().pk,kind='unsubscribe');self.act(row,'advance',minutes=1440)
        self.assertFalse(Address.objects.exists());self.assertFalse(EmailExclusion.objects.exists())
        self.assertFalse(EmailSubscription.objects.filter(is_test=False).exists());self.assertEqual(AutomationSimulationClock.objects.get().clock_at,clock)

    def test_api_validation_read_only_get_filters_and_csrf(self):
        row=self.start();before=row.logs.count()
        r=self.client.get(f'/api/rehearsals/{row.pk}?kind=prepared&direction=asc');self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['pagination']['total'],1);self.assertEqual(row.logs.count(),before)
        self.assertFalse(r.json()['can_send']);self.assertIsNone(r.json()['delivered']);self.assertIn('no-store',r['Cache-Control'])
        self.assertEqual(self.client.get(f'/api/rehearsals/{row.pk}?kind=bad').status_code,400)
        self.assertEqual(self.client.get(f'/api/rehearsals/{row.pk}?page=no').status_code,400)
        self.assertEqual(HttpClient(enforce_csrf_checks=True).post('/api/rehearsals',{},content_type='application/json').status_code,403)
        for payload in ({'action':'tick','send':True},{'action':'advance','minutes':-1}):
            response=self.client.post(f'/api/rehearsals/{row.pk}',{'mode':'simulation','test_only':True,**payload},content_type='application/json')
            self.assertEqual(response.status_code,400)
        other=self.start();letter=row.letters.get()
        self.assertEqual(self.client.get(f'/api/rehearsals/{other.pk}/letters/{letter.pk}').status_code,404)
        d=self.client.get(f'/api/rehearsals/{row.pk}/letters/{letter.pk}').json()
        self.assertEqual(d['snapshot']['rehearsal_id'],row.pk);self.assertFalse(d['sent'])

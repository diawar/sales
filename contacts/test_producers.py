import json
from email.utils import format_datetime
from datetime import timedelta
from unittest.mock import Mock,patch
from django.test import TestCase,SimpleTestCase
from django.utils import timezone
import httpx
from .eondiris_source import normalize,read_page
from .producers import control,run_round
from .models import ProducerPilot,Client,Contact,Address,Audience,ClientFact
from .goszakup_client import SourceError

def row(identifier='123456789012',name='Alpha',**extra):
    return {'bin_iin':identifier,'company_name':name,'email':'<a@example.com','phone':'+7 701 123 45 67','website':'example.com','oked_codes':'26200','oked_names':'Производство компьютеров','region_kato':'г.Алматы','production_address':'Пример адреса','is_active':True,**extra}

def page(rows,next_page=False,number=1):
    return {'success':True,'data':rows,'meta':{'page':number,'limit':20,'total':100,'totalOrganizations':20,'hasNextPage':next_page}}

class ProducerSourceTests(SimpleTestCase):
    def test_public_endpoint_and_bounded_page(self):
        def respond(request):
            self.assertEqual(request.url.host,'e-ondiris.gov.kz')
            self.assertEqual(request.url.path,'/awp-api/registry-front')
            self.assertEqual(request.url.params['limit'],'50')
            self.assertNotIn('Authorization',request.headers)
            return httpx.Response(200,json=page([row()]))
        self.assertEqual(len(read_page(1,httpx.MockTransport(respond))['data']),1)
        with self.assertRaises(SourceError):read_page(0)

    def test_schema_drift_rejected(self):
        with self.assertRaises(SourceError):read_page(1,httpx.MockTransport(lambda request:httpx.Response(200,json={'success':True,'data':[],'meta':{}})))

    def test_retry_after_http_date(self):
        after=format_datetime(timezone.now()+timedelta(minutes=10))
        with self.assertRaises(SourceError) as caught:read_page(1,httpx.MockTransport(lambda request:httpx.Response(429,headers={'Retry-After':after})))
        self.assertGreater(caught.exception.retry_after,590)

    def test_stray_symbol_email_and_numbering_plan(self):
        parsed,issues=normalize(row())
        self.assertEqual(parsed['emails'],['a@example.com'])
        self.assertEqual(parsed['phones'][0]['kind'],'mobile')
        self.assertEqual(parsed['profile']['city'],'Алматы')
        self.assertEqual(parsed['profile']['websites'],['https://example.com'])
        self.assertEqual(issues,[])

    def test_missing_contacts_retained_and_district_not_guessed_as_city(self):
        parsed,_=normalize(row(email=None,phone=None,website=None,region_kato='Каратауский район'))
        self.assertEqual(parsed['emails'],[]);self.assertEqual(parsed['phones'],[])
        self.assertNotIn('city',parsed['profile'])

    def test_bad_identity_and_inactive(self):
        with self.assertRaises(ValueError):normalize(row(identifier='bad'))
        self.assertIsNone(normalize(row(is_active=False))[0])


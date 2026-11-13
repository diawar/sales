from django.test import override_settings as _legacy_auth
import json
from datetime import timedelta
from email.utils import format_datetime
import httpx
from django.test import TestCase,SimpleTestCase
from django.utils import timezone
from .kaspi_client import KaspiClient,KaspiBlocked,KaspiRetry,KaspiFormat
from .kaspi_collection import control,run_round
from .models import KaspiCollection,KaspiTask,KaspiMerchant,Client,Phone

@_legacy_auth(SALES_AUTH_REQUIRED=False)
class PublicClientTests(SimpleTestCase):
    def test_retry_after_http_date_is_not_retried_early(self):
        retry=format_datetime(timezone.now()+timedelta(hours=2),usegmt=True)
        client=KaspiClient(httpx.MockTransport(lambda r:httpx.Response(429,headers={'Retry-After':retry})))
        with self.assertRaises(KaspiRetry) as caught:client.catalogue('cpus',0,'750000000')
        self.assertGreater(caught.exception.delay,7190)
        client.close()
    def test_real_request_shape_and_minimal_business_fields(self):
        seen=[]
        def handler(request):
            seen.append(request)
            if 'pl/results' in request.url.path:
                return httpx.Response(200,json={'data':[{'id':'123','shopLink':'/p/cpu-123/','categoryCodes':['CPUs'],'price':999}]})
            if 'offer-view' in request.url.path:
                return httpx.Response(200,json={'total':1,'offers':[{'merchantId':'Pulser','merchantName':'Shop','price':999}]})
            return httpx.Response(200,json={'phone':'7473606088','formattedPhone':'+7 (747) 360-60-88'})
        client=KaspiClient(httpx.MockTransport(handler))
        self.assertEqual(client.catalogue('cpus',0,'750000000')[0],{'id':'123','url':'https://kaspi.kz/shop/p/cpu-123/'})
        self.assertEqual(client.offers('123','https://kaspi.kz/shop/p/cpu-123/',0,'750000000')['rows'][0]['id'],'Pulser')
        self.assertEqual(client.support('Pulser','750000000')[0]['kind'],'mobile')
        self.assertEqual(seen[1].method,'POST')
        self.assertEqual(json.loads(seen[1].content)['limit'],5)
        self.assertEqual(seen[2].url.params['c'],'750000000')
        self.assertTrue(all(r.headers.get('Referer','').startswith('https://kaspi.kz/shop/') for r in seen))
        self.assertTrue(all('Cookie' not in r.headers for r in seen));client.close()
    def test_errors_are_not_treated_as_empty_catalogues(self):
        for status,expected in [(403,KaspiBlocked),(429,KaspiRetry),(500,KaspiRetry)]:
            client=KaspiClient(httpx.MockTransport(lambda r:httpx.Response(status,headers={'Retry-After':'600'})))
            with self.assertRaises(expected):client.catalogue('cpus',0,'750000000')
            client.close()
        client=KaspiClient(httpx.MockTransport(lambda r:httpx.Response(200,json={'wrong':[]})))
        with self.assertRaises(KaspiFormat):client.catalogue('cpus',0,'750000000')
        client.close()
    def test_foreign_product_url_and_wrong_category_rejected(self):
        for item in [{'id':'1','shopLink':'https://evil.example/shop/p/cpu-1/','categoryCodes':['CPUs']},
                     {'id':'1','shopLink':'/p/cpu-1/','categoryCodes':['Phones']}]:
            client=KaspiClient(httpx.MockTransport(lambda r:httpx.Response(200,json={'data':[item]})))
            with self.assertRaises(KaspiFormat):client.catalogue('cpus',0,'750000000')
            client.close()

class Reader:
    calls=0
    def catalogue(self,*args):self.calls+=1;return [{'id':'123','url':'https://kaspi.kz/shop/p/cpu-123/'}]
    def offers(self,*args):self.calls+=1;return {'rows':[{'id':'Pulser','name':'Shop'}],'total':1}
    def details(self,*args):self.calls+=1;return {'merchant_id':'Pulser','shop_name':'Shop','addresses':['Казахстан, г.Шымкент, адрес']}
    def support(self,*args):self.calls+=1;return [{'number':'+77473606088','raw':'+7 (747) 360-60-88','kind':'mobile'}]

@_legacy_auth(SALES_AUTH_REQUIRED=False)
class AutomaticCollectionTests(TestCase):
    def setUp(self):
        control('start')
        KaspiTask.objects.exclude(key='cpus:0').delete()
        self.reader=Reader()
    def due(self):KaspiCollection.objects.filter(pk=1).update(next_request_at=None)
    def test_pipeline_imports_actual_contacts_and_preserves_cursor(self):
        for expected in range(4):
            self.due();self.assertTrue(run_round(self.reader))
        self.assertEqual(Client.objects.get().identity_key,'kaspi:merchant:Pulser')
        self.assertEqual(Client.objects.get().city,'Шымкент')
        self.assertEqual(Phone.objects.count(),1)
        self.assertTrue(KaspiTask.objects.filter(kind='catalog',key='cpus:1',state='queued').exists())
    def test_rate_limit_and_pause_do_not_lose_work(self):
        self.assertTrue(run_round(self.reader));calls=self.reader.calls
        self.assertFalse(run_round(self.reader));self.assertEqual(self.reader.calls,calls)
        control('pause');self.due();self.assertFalse(run_round(self.reader))
        self.assertEqual(self.reader.calls,calls)
        control('start');self.assertTrue(KaspiTask.objects.filter(kind='offers',state='queued').exists())
    def test_access_denied_pauses_and_resumes_same_task(self):
        self.reader.catalogue=lambda *args:(_ for _ in ()).throw(KaspiBlocked('blocked'))
        self.assertFalse(run_round(self.reader))
        self.assertFalse(KaspiCollection.objects.get().enabled)
        self.assertEqual(KaspiTask.objects.get(key='cpus:0').state,'blocked')
        control('start');self.assertEqual(KaspiTask.objects.get(key='cpus:0').state,'queued')
    def test_retry_after_is_respected(self):
        self.reader.catalogue=lambda *args:(_ for _ in ()).throw(KaspiRetry('slow',600))
        run_round(self.reader)
        self.assertGreater(KaspiCollection.objects.get().next_request_at,timezone.now()+timedelta(seconds=590))
        self.assertTrue(KaspiCollection.objects.get().enabled)
    def test_two_workers_do_not_request_under_another_lease(self):
        KaspiCollection.objects.update(lease='other',lease_until=timezone.now()+timedelta(minutes=1))
        self.assertFalse(run_round(self.reader));self.assertEqual(self.reader.calls,0)
    def test_pause_while_request_is_in_flight_discards_result(self):
        def read(*args):control('pause');return [{'id':'123','url':'https://kaspi.kz/shop/p/cpu-123/'}]
        self.reader.catalogue=read
        self.assertFalse(run_round(self.reader))
        self.assertFalse(KaspiTask.objects.filter(kind='offers').exists())
        self.assertEqual(KaspiTask.objects.get(key='cpus:0').state,'queued')
    def test_offer_pagination_is_queued_and_duplicate_merchants_are_not_requeued(self):
        run_round(self.reader);self.due()
        self.reader.offers=lambda *args:{'rows':[{'id':'Pulser','name':'Shop'}],'total':6}
        run_round(self.reader)
        self.assertTrue(KaspiTask.objects.filter(kind='offers',key='cpus:123:1',state='queued').exists())
        self.assertEqual(KaspiTask.objects.filter(kind='details',key='Pulser').count(),1)
    def test_repeated_catalogue_page_does_not_loop_or_advance(self):
        run_round(self.reader)
        KaspiTask.objects.filter(kind='offers').update(state='done')
        self.due();run_round(self.reader)
        self.assertFalse(KaspiCollection.objects.get().enabled)
        self.assertEqual(KaspiTask.objects.get(key='cpus:1').state,'blocked')
        self.assertFalse(KaspiTask.objects.filter(key='cpus:2').exists())
    def test_retry_exhaustion_retains_task_for_review(self):
        KaspiTask.objects.update(attempts=2)
        self.reader.catalogue=lambda *args:(_ for _ in ()).throw(KaspiRetry('server error'))
        run_round(self.reader)
        self.assertEqual(KaspiTask.objects.get(key='cpus:0').state,'review')
        self.assertTrue(KaspiCollection.objects.get().enabled)

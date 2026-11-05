import json
from pathlib import Path
from tempfile import TemporaryDirectory
from django.test import SimpleTestCase

class OverShopPreviewTests(SimpleTestCase):
    def test_full_review_filters_orders_and_excludes_staff_by_default(self):
        with TemporaryDirectory() as directory,self.settings(BASE_DIR=Path(directory)):
            folder=Path(directory)/'data/overshop';folder.mkdir(parents=True)
            rows=[{'source_customer_id':str(i),'category':'staff' if i==1 else 'person_candidate',
                'purchase_segment':'paid' if i%2 else 'no_orders','orders_total':i,'name':'Client','email':'a@example.com'} for i in range(1,26)]
            (folder/'full-review.json').write_text(json.dumps({'summary':{},'records':rows}),encoding='utf-8')
            result=self.client.get('/api/overshop/preview').json()
            self.assertEqual(result['total'],24)
            self.assertEqual(len(result['preview']),20)
            result=self.client.get('/api/overshop/preview?purchase=no_orders&sort=orders_asc').json()
            self.assertEqual(result['total'],12)
            self.assertEqual(result['preview'][0]['source_customer_id'],'2')
            self.assertEqual(self.client.get('/api/overshop/preview?category=staff').json()['total'],1)
            self.assertEqual(self.client.get('/api/overshop/preview?purchase=invalid').status_code,400)
            self.assertEqual(self.client.post('/api/overshop/preview').status_code,405)
    def test_preview_is_read_only_and_does_not_expose_file_path(self):
        with TemporaryDirectory() as directory,self.settings(BASE_DIR=Path(directory)):
            folder=Path(directory)/'data/overshop';folder.mkdir(parents=True)
            (folder/'buyers-preview.json').write_text(json.dumps({'source':'private-path','records':1,'unique_valid_emails':1,
                'stats':{},'blockers':['Type unknown'],'preview':[{'source_customer_id':'10','name':'Client'}]}),encoding='utf-8')
            response=self.client.get('/api/overshop/preview')
            self.assertEqual(response.status_code,200)
            self.assertFalse(response.json()['connected'])
            self.assertFalse(response.json()['imported'])
            self.assertNotIn('source',response.json())
            self.assertEqual(self.client.post('/api/overshop/preview').status_code,405)
    def test_missing_file_does_not_claim_connection(self):
        with TemporaryDirectory() as directory,self.settings(BASE_DIR=Path(directory)):
            self.assertEqual(self.client.get('/api/overshop/preview').json(),{'available':False,'connected':False})

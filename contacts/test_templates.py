from django.test import override_settings as _legacy_auth
import json
from django.test import TestCase, Client
from .models import EmailTemplate


@_legacy_auth(SALES_AUTH_REQUIRED=False)
class TemplateTests(TestCase):
    def post(self,data,url='/api/templates'):
        return self.client.post(url,json.dumps(data),content_type='application/json')

    def test_create_reload_update_copy_search(self):
        payload={'name':'Акция','subject':'Предложение','preheader':'Подробности','html':'<h1>Привет</h1>'}
        r=self.post(payload);self.assertEqual(r.status_code,201)
        row=r.json();url='/api/templates/'+str(row['id'])
        self.assertEqual(self.client.get(url).json()['html'],payload['html'])
        payload.update(revision=row['revision'],html='<p>Новый текст</p>')
        r=self.post(payload,url);self.assertEqual(r.json()['revision'],2)
        self.assertEqual(self.post(payload,url).status_code,409)
        self.assertEqual(EmailTemplate.objects.get(pk=row['id']).html,payload['html'])
        payload['name']='Копия';self.assertEqual(self.post(payload).status_code,201)
        self.assertEqual(self.client.get('/api/templates?q=Копия').json()['total'],1)

    def test_validation_and_missing(self):
        for data in [[],{'name':''},{'name':42},{'name':'a','html':'x'*500001}]:
            self.assertEqual(self.post(data).status_code,400)
        self.assertEqual(self.client.get('/api/templates/999').status_code,404)
        self.assertEqual(self.client.delete('/api/templates').status_code,405)
        self.assertEqual(EmailTemplate.objects.count(),0)

    def test_html_is_returned_as_json_not_executed(self):
        html='<script>alert(1)</script><img src=x onerror=alert(1)>'
        r=self.post({'name':'HTML','html':html})
        response=self.client.get('/api/templates/'+str(r.json()['id']))
        self.assertEqual(response['Content-Type'],'application/json')
        self.assertEqual(response.json()['html'],html)

    def test_csrf_required(self):
        client=Client(enforce_csrf_checks=True)
        self.assertEqual(client.post('/api/templates',json.dumps({'name':'a'}),content_type='application/json').status_code,403)

    def design(self):
        return {'version':1,'brand':{'name':'OVER','website':'https://example.com','logo':'','contacts':'Адрес','accent':'#0088cc','background':'#eeeeee','surface':'#ffffff','text':'#112233','font':'Arial','width':600},'blocks':[{'type':'heading','title':'Акция','align':'left'}]}

    def test_design_round_trip_and_copy(self):
        payload={'name':'Блоки','html':'<h1>Акция</h1>','design':self.design()}
        row=self.post(payload).json()
        self.assertEqual(self.client.get('/api/templates/'+str(row['id'])).json()['design'],payload['design'])
        copy=self.post(payload).json()
        self.assertNotEqual(row['id'],copy['id'])
        self.assertEqual(copy['design'],row['design'])

    def test_invalid_design_rejected(self):
        for change in [lambda d:d.update(version=2),lambda d:d['brand'].update(accent='red;position:fixed'),lambda d:d['blocks'].append({'type':'script'}),lambda d:d['blocks'][0].update(url='javascript:alert(1)'),lambda d:d['brand'].update(width=9999),lambda d:d.update(blocks=[{'type':'text'}]*101)]:
            design=self.design();change(design)
            self.assertEqual(self.post({'name':'Блоки','design':design}).status_code,400)

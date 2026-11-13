from django.test import override_settings as _legacy_auth
import json
from unittest.mock import patch,Mock
import httpx
from django.test import TestCase,override_settings
from .template_tools import personalize,check_template
from .template_ai import generate
from .models import TemplateAIQuota,EmailTemplate

@_legacy_auth(SALES_AUTH_REQUIRED=False)
class TemplateToolsTests(TestCase):
    def post(self,url,data):return self.client.post('/api/templates/'+url,json.dumps(data),content_type='application/json')

    def test_personalization_is_literal_and_escaped(self):
        rendered,issues=personalize('<p>{{name|клиент}} · {{city|вашем городе}}</p>',{'name':'<img src=x onerror=alert(1)>'},True)
        self.assertIn('&lt;img',rendered);self.assertIn('вашем городе',rendered);self.assertEqual(issues,[])
        self.assertEqual(personalize('{{name|клиент}}',{})[0],'клиент')
        self.assertIn('Неизвестная',personalize('{{password}}',{})[1][0])
        self.assertTrue(personalize('{{name',{})[1])

    def test_checks_unsubscribe_and_unsafe_urls(self):
        report=check_template('', '', '<script>x</script><img src="javascript:alert(1)"><p>{{unknown}}</p>',{})
        codes={f['code'] for f in report['findings']}
        self.assertTrue({'subject','unsubscribe','active','unsafe_url','alt'}<=codes)
        self.assertGreater(report['errors'],3)

    def test_valid_template_and_fallbacks(self):
        html='<html><head><meta name="viewport" content="width=device-width"></head><body><p>Здравствуйте, {{name|клиент}}!</p><a href="{{unsubscribe_url}}">Отписаться</a></body></html>'
        report=check_template('Тема','Описание',html,{})
        self.assertEqual(report['errors'],0);self.assertEqual(report['warnings'],0)
        self.assertIn('example.com',personalize(html,{},True)[0])

    def test_check_api_validation_and_missing_value(self):
        r=self.post('check',{'subject':'Тема','html':'<p>{{name}}</p>'})
        self.assertEqual(r.status_code,200);self.assertGreater(r.json()['warnings'],0)
        for value in [[],{'values':{'password':'x'}},{'html':42}]:self.assertEqual(self.post('check',value).status_code,400)
        self.assertEqual(EmailTemplate.objects.count(),0)

    @override_settings(SALES_AI_KEY='',SALES_AI_MODEL='')
    @patch('contacts.template_ai.httpx.Client')
    def test_no_key_no_request(self,client):
        self.assertFalse(self.client.get('/api/templates/ai').json()['configured'])
        self.assertEqual(self.post('ai',{'action':'write','instruction':'Акция'}).status_code,503)
        client.assert_not_called()

    @override_settings(SALES_AI_KEY='test-not-a-real-key',SALES_AI_MODEL='configured-model')
    @patch('contacts.template_ai.generate',return_value={'subject':'Тема','preheader':'Описание','text':'Текст'})
    def test_ai_quota_and_no_automatic_save(self,generate):
        data={'action':'write','instruction':'Напиши предложение','source':'Условия'}
        r=self.post('ai',data);self.assertEqual(r.status_code,200)
        self.assertEqual(self.post('ai',data).status_code,429)
        self.assertEqual(generate.call_count,1);self.assertEqual(EmailTemplate.objects.count(),0)
        TemplateAIQuota.objects.update(calls=30,next_at=None)
        self.assertEqual(self.post('ai',data).status_code,429)
        self.assertEqual(self.post('ai',{'action':[]}).status_code,400)

    @override_settings(SALES_AI_KEY='test-secret',SALES_AI_MODEL='configured-model')
    @patch('contacts.template_ai.httpx.Client')
    def test_ai_contract_and_structured_result(self,client):
        data={'subject':'Тема','preheader':'Описание','text':'Текст {{name|клиент}}'}
        response=httpx.Response(200,json={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(data)}]}]})
        caller=client.return_value.__enter__.return_value;caller.post.return_value=response
        self.assertEqual(generate('write','Только факты','Исходный текст'),data)
        payload=caller.post.call_args.kwargs['json']
        self.assertFalse(payload['store']);self.assertEqual(payload['text']['format']['type'],'json_schema')
        self.assertNotIn('test-secret',json.dumps(payload));self.assertNotIn('client@example.com',json.dumps(payload))
        caller.post.return_value=httpx.Response(200,json={'status':'incomplete'})
        with self.assertRaises(ValueError):generate('write','x','')
        caller.post.return_value=httpx.Response(401,json={'error':'test-secret'})
        with self.assertRaises(ValueError) as error:generate('write','x','')
        self.assertNotIn('test-secret',str(error.exception))

    @override_settings(SALES_AI_KEY='test',SALES_AI_MODEL='model')
    @patch('contacts.template_ai.httpx.Client')
    def test_ai_timeout_and_refusal(self,client):
        caller=client.return_value.__enter__.return_value
        caller.post.side_effect=httpx.ReadTimeout('timeout')
        with self.assertRaises(ValueError):generate('write','x','')
        caller.post.side_effect=None
        caller.post.return_value=httpx.Response(200,json={'status':'completed','output':[{'type':'message','content':[{'type':'refusal','refusal':'no'}]}]})
        with self.assertRaises(ValueError):generate('write','x','')

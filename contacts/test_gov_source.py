from django.test import SimpleTestCase
from .gov_source import parse_card,canonical_url,parse_phones,read_card
import httpx
from .goszakup_client import SourceError

class GovParserTests(SimpleTestCase):
    def card(self,rows):return '<h1>Госорган</h1><div class="contacts-detail"><table>'+rows+'</table></div>'
    def test_scoped_contacts_and_purpose(self):
        html=self.card('<tr><td>Канцелярия</td><td>+7 (7172) 71-74-67</td></tr><tr><td>Email</td><td>office@example.kz</td></tr>')+'<p>news@example.kz</p>'
        row=parse_card(html,'https://www.gov.kz/org/test')
        self.assertEqual(row['emails'],['office@example.kz'])
        self.assertEqual(row['phones'][0]['purpose'],'Канцелярия')
        self.assertEqual(row['phones'][0]['kind'],'landline')
    def test_no_email_is_valid(self):
        self.assertEqual(parse_card(self.card('<tr><td>Адрес</td><td>Астана</td></tr>'),'https://www.gov.kz/org/test')['emails'],[])
    def test_missing_block_is_error(self):
        with self.assertRaises(ValueError):parse_card('<title>Ошибка</title>','https://www.gov.kz/org/test')
    def test_url_boundaries(self):
        for url in ['http://www.gov.kz/org/test','https://www.gov.kz.evil/org/test','https://www.gov.kz/org/test/news/1','https://www.gov.kz/org/test?redirect=x']:
            with self.assertRaises(ValueError):canonical_url(url)
    def test_no_merge_by_email(self):
        html=self.card('<tr><td>Email</td><td>shared@example.kz</td></tr>')
        self.assertNotEqual(parse_card(html,'https://www.gov.kz/org/one')['identity_key'],parse_card(html,'https://www.gov.kz/org/two')['identity_key'])
    def test_abbreviated_numbers_and_whitespace(self):
        phones=parse_phones('+7 (7172)\xa0   71-74-67 , 71-81-09 , 71-75-97')
        self.assertEqual({p['number'] for p in phones},{'+77172717467','+77172718109','+77172717597'})
        self.assertEqual(parse_phones('71-81-09, 71-75-97'),[])
    def test_service_number(self):
        self.assertEqual(parse_phones('+7 800 0803437')[0]['kind'],'service')
    def test_contact_page_header_not_generic_title(self):
        html='<title>Контакты</title><a class="ministry-header-top__title">Акимат города Астаны</a><div class="ministry-contacts-card"><div class="contacts inner-html"><table><tr><td>Адрес</td><td>г. Астана, улица 11</td></tr></table></div></div>'
        row=parse_card(html,'https://www.gov.kz/org/astana/contacts?lang=ru')
        self.assertEqual(row['name'],'Акимат города Астаны');self.assertEqual(row['city'],'Астана')
        self.assertEqual(row['identity_key'],'gov:astana');self.assertEqual(row['source_url'],'https://www.gov.kz/org/astana/contacts?lang=ru')
    def test_http_rate_limit_and_redirect_not_followed(self):
        for status in (302,429):
            calls=[]
            def handler(request):
                calls.append(str(request.url));return httpx.Response(status,headers={'Retry-After':'600','Location':'https://example.com'})
            with self.assertRaises(SourceError) as caught:read_card('https://www.gov.kz/org/test',httpx.MockTransport(handler))
            self.assertEqual(len(calls),1);self.assertEqual(caught.exception.retry_after,600)
class GovParagraphTests(SimpleTestCase):
    def test_empty_contact_card_has_explicit_reason(self):
        html='<h1>Маслихат</h1><div class="ministry-contacts-page"><div class="ministry-contacts-card">Социальные сети</div></div>'
        with self.assertRaisesRegex(ValueError,'не опубликованы'):
            parse_card(html,'https://www.gov.kz/org/test')
    def test_empty_table_layout_columns(self):
        html='<h1>Агентство</h1><div class="contacts-detail"><table><tr><td>Email</td><td>office@example.kz</td><td>&nbsp;</td></tr><tr><td> </td><td> </td><td> </td></tr></table></div>'
        result=parse_card(html,'https://www.gov.kz/org/test')
        self.assertEqual(result['emails'],['office@example.kz'])
        self.assertEqual(len(result['source_rows']),1)
    def test_plain_contact_paragraphs(self):
        html='<h1>Маслихат</h1><div class="ministry-contacts-card"><div class="contacts inner-html"><p>Посёлок, улица, 11</p><p>тел.: +7 (72331) 22790, office@example.kz</p></div></div><p>outside@example.kz</p>'
        result=parse_card(html,'https://www.gov.kz/org/test')
        self.assertEqual(result['emails'],['office@example.kz'])
        self.assertEqual(len(result['phones']),1)
    def test_labeled_contact_paragraphs(self):
        from contacts.gov_source import parse_card
        html='<h1>Акимат</h1><div class="ministry-contacts-card"><div class="contacts inner-html"><p><strong>Адрес:</strong> г. Алматы, площадь, 4</p><p><strong>Справочная служба:</strong> +7 (727) 338 44 94</p><p><strong>Email:</strong> office@example.kz</p></div></div><footer>other@example.kz</footer>'
        result=parse_card(html,'https://www.gov.kz/org/almaty?lang=ru')
        self.assertEqual(result['emails'],['office@example.kz'])
        self.assertEqual(result['city'],'Алматы')
        self.assertEqual(len(result['phones']),1)
        nested=html.replace('<strong>Email:</strong> office@example.kz','<font><strong>Email:</strong> office@example.kz</font>')
        self.assertEqual(parse_card(nested,'https://www.gov.kz/org/almaty')['emails'],['office@example.kz'])


from django.test import SimpleTestCase
from .directory_sample import parse_vspravke

class DirectorySampleTests(SimpleTestCase):
    def test_scoped_and_deduplicated_contacts(self):
        html='<div itemtype="http://schema.org/Organization"><h1 itemprop="name">Магазин</h1><span itemprop="telephone">+7(727)328-04-93</span><meta itemprop="telephone" content="+7(727)328-04-93"><span itemprop="url">https://example.kz</span><meta itemprop="url" content="https://kz.vspravke.com/almaty/company/1"><span itemprop="email">office@example.kz</span></div><span itemprop="telephone">+77011234567</span><span itemprop="email">other@example.kz</span>'
        row=parse_vspravke(html,'https://kz.vspravke.com/almaty/company/1')
        self.assertEqual(row['emails'],['office@example.kz']);self.assertEqual(len(row['phones']),1)
        self.assertEqual(row['websites'],['https://example.kz'])
    def test_missing_contact_fields_not_guessed(self):
        row=parse_vspravke('<div itemtype="http://schema.org/Organization"><h1 itemprop="name">Имя</h1></div>','https://kz.vspravke.com/almaty/company/1')
        self.assertEqual(row['emails'],[]);self.assertEqual(row['phones'],[]);self.assertEqual(row['city'],'')
    def test_schema_change_is_error(self):
        with self.assertRaises(ValueError):parse_vspravke('<h1>Страница</h1>','https://kz.vspravke.com/almaty/company/1')

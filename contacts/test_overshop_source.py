import csv
import io
from html import escape
from django.test import SimpleTestCase
from .overshop_source import prepare_users, parse_buyers,prepare_user_csv,parse_subscribers_csv


def html_table(headers, rows):
    return '<table>'+''.join('<tr>'+''.join('<td>'+escape(str(v))+'</td>' for v in row)+'</tr>' for row in [headers, *rows])+'</table>'


class AuthorisedCsvTests(SimpleTestCase):
    def test_customer_csv_preserves_quoted_names_and_source_identity(self):
        stream=io.StringIO();writer=csv.writer(stream)
        writer.writerow(['CUSTOMER_ID','FIRST_NAME','LAST_NAME','EMAIL','PHONE','MOBILE','CITY','COMPANY','REGISTERED_AT','ORDERS_TOTAL','ORDERS_PAID','ORDERS_CANCELLED','COMPANY_ORDERS','GROUP_NAMES'])
        writer.writerow(['7','A <B>, C','','a@example.com','','','','','2023-01-01',1,0,0,0,''])
        row=prepare_user_csv(stream.getvalue())[0]
        self.assertEqual(row['name'],'A <B>, C')
        self.assertEqual(row['identity_key'],'overshop:user:7')
        self.assertEqual(row['purchase_segment'],'unpaid')

    def test_subscription_flags_dates_and_empty_user_are_not_inferred(self):
        text='SUBSCRIBER_ID,CUSTOMER_ID,EMAIL,ACTIVE,CONFIRMED,DATE_INSERT,DATE_UPDATE,DATE_CONFIRM,RUBRIC_IDS\n5,,a@example.com,Y,N,2023-01-01,2026-11-11,,1|2\n'
        row=parse_subscribers_csv(text)[0]
        self.assertEqual(row['customer_id'],'')
        self.assertFalse(row['confirmed'])
        self.assertEqual(row['confirmed_at'],'')
        self.assertEqual(row['rubrics'],['1','2'])
        with self.assertRaisesMessage(ValueError,'Неизвестный статус'):
            parse_subscribers_csv(text.replace(',Y,N,',',Y,unknown,'))


class FullUserExportTests(SimpleTestCase):
    def exports(self):
        headers=['ID','Имя','Фамилия','E-Mail','Личный телефон','Личный мобильный','Город проживания','Компания','Дата регистрации']
        users=html_table(headers, [[str(i),'Клиент','','person@example.com','','','','',''] for i in range(1,6)])
        stream=io.StringIO();writer=csv.writer(stream)
        writer.writerow(['CUSTOMER_ID','ORDERS_TOTAL','ORDERS_PAID','ORDERS_CANCELLED','COMPANY_ORDERS','GROUP_NAMES'])
        writer.writerows([['1',0,0,0,0,'Администраторы'],['2',0,0,0,0,'Зарегистрированные пользователи'],
                          ['3',2,0,0,1,''],['4',2,0,2,0,''],['5',3,1,1,0,'']])
        return users,stream.getvalue()

    def test_join_and_distinct_order_segments(self):
        rows=prepare_users(*self.exports())
        self.assertEqual([r['purchase_segment'] for r in rows],['no_orders','no_orders','unpaid','cancelled','paid'])
        self.assertEqual(rows[0]['category'],'staff')
        self.assertEqual(rows[2]['category'],'organization_review')
        self.assertEqual(rows[1]['identity_key'],'overshop:user:2')
        self.assertTrue(all(r['marketing_consent']=='Неизвестно' for r in rows))

    def test_missing_metadata_is_not_treated_as_no_orders(self):
        users,orders=self.exports()
        with self.assertRaisesMessage(ValueError,'Нет статистики'):
            prepare_users(users,'\n'.join(orders.splitlines()[:-1]))

    def test_duplicate_ids_and_contradictory_counts_rejected(self):
        users,orders=self.exports()
        with self.assertRaises(ValueError):prepare_users(users,orders+orders.splitlines()[1]+'\n')
        with self.assertRaisesMessage(ValueError,'Противоречивая'):
            prepare_users(users,orders.replace('5,3,1,1,0','5,3,3,1,0'))

    def test_company_is_review_not_confirmed_organization(self):
        users,orders=self.exports()
        rows=prepare_users(users.replace('<td>Клиент</td>','<td>ТОО Пример</td>'),orders)
        self.assertEqual(rows[1]['category'],'organization_review')
        self.assertIn('форма',' '.join(rows[1]['reasons']))

    def test_buyer_duplicate_with_contact_conflict_rejected(self):
        headers=['ID','Покупатель','E-mail','Телефон','Группы покупателя','Сумма полученных оплат']
        row=['1','Клиент','a@example.com','','','100']
        other=[*row[:-1],'200']
        self.assertEqual(parse_buyers(html_table(headers,[row,other]))[0]['source_rows'],2)
        other[2]='b@example.com'
        with self.assertRaisesMessage(ValueError,'конфликт контактных'):
            parse_buyers(html_table(headers,[row,other]))

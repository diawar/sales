"""Read-only Bitrix buyer export parsing and conservative review categories."""
import re
import csv
import io
from html import escape
from html.parser import HTMLParser
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

class BuyerTable(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.rows=[];self.row=None;self.cell=None
    def handle_starttag(self,tag,attrs):
        if tag=='tr':self.row=[]
        elif tag in {'td','th'} and self.row is not None:self.cell=[]
        elif tag=='br' and self.cell is not None:self.cell.append('\n')
    def handle_data(self,value):
        if self.cell is not None:self.cell.append(value)
    def handle_endtag(self,tag):
        if tag in {'td','th'} and self.cell is not None:
            self.row.append(''.join(self.cell).strip());self.cell=None
        elif tag=='tr' and self.row is not None:self.rows.append(self.row);self.row=None


def parse_buyers(text):
    if len(text.encode('utf-8'))>10_000_000:raise ValueError('Выгрузка больше 10 МБ.')
    parser=BuyerTable();parser.feed(text)
    if not parser.rows:raise ValueError('Нет таблицы покупателей.')
    headers=parser.rows[0]
    if not {'ID','Покупатель','E-mail','Телефон','Группы покупателя'}.issubset(headers):
        raise ValueError('Выгрузка должна содержать ID, покупателя, email, телефон и группы.')
    if len(set(headers))!=len(headers):raise ValueError('Повторные заголовки.')
    records=[];seen={}
    for line,values in enumerate(parser.rows[1:],2):
        if len(values)!=len(headers):raise ValueError(f'Строка {line}: неверное число полей.')
        row=dict(zip(headers,values));identifier=row['ID'].strip()
        if not re.fullmatch(r'[0-9]+',identifier) or int(identifier)<1:raise ValueError(f'Строка {line}: неверный ID.')
        identity_fields={key:value for key,value in row.items() if key not in {'Сумма полученных оплат','Кол-во полностью оплаченных заказов','Кол-во частично оплаченных заказов'}}
        if identifier in seen:
            previous,record_index=seen[identifier]
            if identity_fields!=previous:raise ValueError(f'Строка {line}: конфликт контактных данных одного ID.')
            records[record_index]['source_rows']+=1
            continue
        seen[identifier]=(identity_fields,len(records))
        name=row['Покупатель'].strip();email=row['E-mail'].strip().lower()
        groups=[g.strip() for g in row['Группы покупателя'].splitlines() if g.strip()]
        category='person_candidate';reasons=[]
        if any(g.casefold()=='администраторы' for g in groups):
            category='staff';reasons.append('Член группы «Администраторы»')
        elif re.search(r'(?<!\w)(?:ТОО|ООО|ЗАО|ОАО|АО|ИП|LLP|LLC|LTD)(?!\w)',name,re.I):
            category='organization_review';reasons.append('В названии явно указана организационно-правовая форма; требуется проверка')
        elif email.rsplit('@',1)[-1] in {'over-shop.kz','overclockers.kz'} or re.search(r'(?<!\w)(?:тест|test|админ|admin)(?!\w)',name,re.I):
            category='service_review';reasons.append('Служебный домен или тестовое имя; требуется проверка')
        else:reasons.append('Выгрузка не содержит явных признаков сотрудника или организации; физлицо не подтверждено')
        issues=[]
        try:validate_email(email)
        except ValidationError:issues.append('Некорректный или пустой email')
        if not name:issues.append('Не указано имя')
        records.append({'source_customer_id':identifier,'identity_key':'overshop:user:'+identifier,'source_rows':1,'name':name,
            'email':email,'phone':row['Телефон'].strip(),'city':row.get('Город проживания') or row.get('Город') or None,
            'registered_at':row.get('Дата регистрации',''),'groups':groups,'category':category,'reasons':reasons,
            'issues':issues,'marketing_consent':'Неизвестно'})
    return records


def prepare_user_csv(text):
    """Read the authorised SELECT export, without credentials or order amounts."""
    reader=csv.DictReader(io.StringIO(text))
    mapping={'ID':'CUSTOMER_ID','Имя':'FIRST_NAME','Фамилия':'LAST_NAME','E-Mail':'EMAIL',
             'Личный телефон':'PHONE','Личный мобильный':'MOBILE','Город проживания':'CITY',
             'Компания':'COMPANY','Дата регистрации':'REGISTERED_AT'}
    required=set(mapping.values())|{'ORDERS_TOTAL','ORDERS_PAID','ORDERS_CANCELLED','COMPANY_ORDERS','GROUP_NAMES'}
    if not required.issubset(reader.fieldnames or []):raise ValueError('Неполная выгрузка клиентов.')
    rows=list(reader)
    if not rows or len(rows)>100000:raise ValueError('Неверный размер выгрузки клиентов.')
    table='<table><tr>'+''.join('<th>'+escape(k)+'</th>' for k in mapping)+'</tr>'
    for row in rows:
        if None in row or any(row.get(k) is None for k in required):raise ValueError('Повреждённая строка CSV.')
        table+='<tr>'+''.join('<td>'+escape(row[v])+'</td>' for v in mapping.values())+'</tr>'
    return prepare_users(table+'</table>',text)


def parse_subscribers_csv(text):
    reader=csv.DictReader(io.StringIO(text))
    required={'SUBSCRIBER_ID','CUSTOMER_ID','EMAIL','ACTIVE','CONFIRMED','DATE_INSERT','DATE_UPDATE','DATE_CONFIRM','RUBRIC_IDS'}
    if not required.issubset(reader.fieldnames or []):raise ValueError('Неполная выгрузка подписчиков.')
    records=[];seen=set()
    for row in reader:
        identifier=row['SUBSCRIBER_ID'];customer=row['CUSTOMER_ID']
        if not re.fullmatch(r'[1-9][0-9]*',identifier or '') or identifier in seen:raise ValueError('Неверный или повторный ID подписки.')
        seen.add(identifier)
        if customer not in {'','NULL','0'} and not re.fullmatch(r'[1-9][0-9]*',customer or ''):raise ValueError('Неверный ID аккаунта подписчика.')
        if row['ACTIVE'] not in {'Y','N'} or row['CONFIRMED'] not in {'Y','N'}:raise ValueError('Неизвестный статус подписки.')
        email=row['EMAIL'].strip().lower()
        try:validate_email(email)
        except ValidationError:raise ValueError('Некорректный email подписчика.')
        rubrics=[r for r in row['RUBRIC_IDS'].split('|') if r]
        if any(not re.fullmatch(r'[1-9][0-9]*',r) for r in rubrics):raise ValueError('Неверная рубрика.')
        records.append({'id':identifier,'customer_id':customer if customer not in {'','NULL','0'} else '',
                        'email':email,'active':row['ACTIVE']=='Y','confirmed':row['CONFIRMED']=='Y',
                        'rubrics':sorted(set(rubrics),key=int),'created_at':row['DATE_INSERT'],
                        'updated_at':row['DATE_UPDATE'],'confirmed_at':row['DATE_CONFIRM']})
    if len(records)>100000:raise ValueError('Слишком много подписок.')
    return records


PURCHASE_LABELS = {
    'paid': 'Есть оплаченные покупки',
    'unpaid': 'Есть заказы без полной оплаты',
    'cancelled': 'Только отменённые заказы',
    'no_orders': 'Не делали заказов',
}
CATEGORY_LABELS = {
    'person_candidate': 'Кандидаты в физические лица',
    'staff': 'Администраторы — исключены',
    'organization_review': 'Организации — на проверку',
    'service_review': 'Служебные записи — на проверку',
}


def prepare_users(users_html, orders_csv, buyers=None):
    """Join complete exports by source ID. Never infer no orders from missing data."""
    parser = BuyerTable()
    parser.feed(users_html)
    required = {'ID', 'Имя', 'Фамилия', 'E-Mail', 'Личный телефон',
                'Личный мобильный', 'Город проживания', 'Компания', 'Дата регистрации'}
    if not parser.rows or not required.issubset(parser.rows[0]):
        raise ValueError('Неполный экспорт пользователей.')
    headers = parser.rows[0]
    if len(set(headers)) != len(headers):
        raise ValueError('Повторные заголовки пользователей.')
    order_reader = csv.DictReader(io.StringIO(orders_csv))
    count_fields = ['ORDERS_TOTAL', 'ORDERS_PAID', 'ORDERS_CANCELLED', 'COMPANY_ORDERS']
    if not {'CUSTOMER_ID', 'GROUP_NAMES', *count_fields}.issubset(order_reader.fieldnames or []):
        raise ValueError('Неполная статистика заказов.')
    metadata = {}
    for row in order_reader:
        identifier = row['CUSTOMER_ID']
        if not re.fullmatch(r'[1-9][0-9]*', identifier or '') or identifier in metadata:
            raise ValueError('Повторный или неверный ID статистики.')
        if any(not re.fullmatch(r'[0-9]+', row.get(k) or '') for k in count_fields):
            raise ValueError('Неверное количество заказов.')
        counts = {k: int(row[k]) for k in count_fields}
        total, paid, cancelled, company = (counts[k] for k in count_fields)
        if paid + cancelled > total or company > total:
            raise ValueError('Противоречивая статистика заказов.')
        metadata[identifier] = (counts, row['GROUP_NAMES'])
    buyer_map = {r['source_customer_id']: r for r in (buyers or [])}
    records = []
    seen = set()
    for values in parser.rows[1:]:
        if len(values) != len(headers):
            raise ValueError('Неверное число полей пользователя.')
        row = dict(zip(headers, values))
        identifier = row['ID']
        if not re.fullmatch(r'[1-9][0-9]*', identifier) or identifier in seen:
            raise ValueError('Повторный или неверный ID пользователя.')
        seen.add(identifier)
        if identifier not in metadata:
            raise ValueError('Нет статистики для одного из ID — требуется полная выгрузка.')
        counts, group_names = metadata[identifier]
        total, paid, cancelled, company_orders = (counts[k] for k in count_fields)
        groups = [g.strip() for g in group_names.split('|') if g.strip()]
        buyer = buyer_map.get(identifier, {})
        name = ' '.join(v for v in [row['Имя'], row['Фамилия']] if v).strip()
        name = name or buyer.get('name', '')
        email = row['E-Mail'].strip().lower()
        company = row['Компания'].strip()
        category = 'person_candidate'
        reasons = []
        if 'Администраторы' in groups:
            category = 'staff'
            reasons.append('Группа «Администраторы»: исключить из клиентской загрузки; занятость отдельно не подтверждена')
        elif company or company_orders or re.search(r'(?<!\w)(?:ТОО|ООО|ЗАО|ОАО|АО|ИП|LLP|LLC|LTD)(?!\w)', name, re.I):
            category = 'organization_review'
            if company:
                reasons.append('Заполнено поле компании; это может быть место работы физлица')
            if company_orders:
                reasons.append('Есть заказ с типом плательщика «Юридическое лицо»; требуется проверка владельца аккаунта')
            if not company and not company_orders:
                reasons.append('В имени указана организационно-правовая форма')
        elif email.rsplit('@', 1)[-1] in {'over-shop.kz', 'overclockers.kz'} or re.search(r'(?<!\w)(?:тест|test|админ|admin)(?!\w)', name, re.I):
            category = 'service_review'
            reasons.append('Служебный домен или тестовое имя; требуется проверка')
        else:
            reasons.append('Явных признаков организации или администратора нет; физлицо не подтверждено')
        issues = []
        try:
            validate_email(email)
        except ValidationError:
            issues.append('Некорректный или пустой email')
        if not name:
            issues.append('Не указано имя')
        if buyer and buyer['email'] != email:
            issues.append('Email различается в двух выгрузках; требуется проверка')
        segment = 'paid' if paid else 'no_orders' if total == 0 else 'cancelled' if cancelled == total else 'unpaid'
        phones = list(dict.fromkeys(p for p in [row['Личный мобильный'].strip(), row['Личный телефон'].strip(), buyer.get('phone', '')] if p))
        records.append({
            'source_customer_id': identifier, 'identity_key': 'overshop:user:' + identifier,
            'name': name, 'email': email, 'phone': '; '.join(phones), 'phones': phones,
            'city': row['Город проживания'], 'company': company, 'registered_at': row['Дата регистрации'],
            'groups': groups, 'category': category, 'reasons': reasons, 'issues': issues,
            'marketing_consent': 'Неизвестно', 'orders_total': total, 'orders_paid': paid,
            'orders_cancelled': cancelled, 'company_orders': company_orders,
            'purchase_segment': segment, 'purchase_label': PURCHASE_LABELS[segment],
        })
    if seen != set(metadata) or not set(buyer_map).issubset(seen):
        raise ValueError('Состав ID выгрузок не совпадает.')
    return records

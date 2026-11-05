"""Public DFO table adapter. Only organization rows and their contact columns."""
import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit
import httpx
from bs4 import BeautifulSoup
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from .phones import parse_phone
from .gov_source import parse_phones
from .goszakup_client import SourceError

ENDPOINT='https://opi.dfo.kz/ru/opi/list'
PAGE_SIZE=15


def parse_page(html,page):
    soup=BeautifulSoup(html,'html.parser')
    totals=re.findall(r'Всего записей\s*([0-9\s\xa0]+)',soup.get_text(' ',strip=True))
    if not totals:raise SourceError('ДФО: не найден размер каталога; курсор сохранён.')
    total=int(re.sub(r'\s','',totals[0]))
    active=[node.get_text(' ',strip=True) for node in soup.select('.pagination .active')]
    if not active or any(value!=str(page) for value in active):
        raise SourceError('ДФО: источник не подтвердил запрошенную страницу.')
    expected=['бин','наименование (рус.)','адрес','e-mail','телефон','мобильный']
    table=None
    for candidate in soup.select('table'):
        headers=[c.get_text(' ',strip=True).casefold() for c in candidate.select('thead th')]
        if all(key in headers for key in expected):table=candidate;break
    if table is None:raise SourceError('ДФО: изменился формат таблицы; курсор сохранён.')
    rows=[];issues=[]
    for tr in table.select('tbody tr'):
        cells=tr.find_all('td',recursive=False)
        if len(cells)!=len(headers):raise SourceError('ДФО: изменилось число колонок.')
        values=dict(zip(headers,[c.get_text(' ',strip=True) for c in cells]))
        identifier=values['бин'];name=values['наименование (рус.)']
        if not re.fullmatch(r'[0-9]{12}',identifier) or not name or len(name)>300:
            raise SourceError('ДФО: не подтверждены БИН и название строки.')
        anchor=tr.select_one('a[href]')
        target=urljoin(ENDPOINT,anchor['href']) if anchor else ''
        parsed=urlsplit(target)
        if parsed.scheme!='https' or parsed.netloc!='opi.dfo.kz' or not re.fullmatch(r'/ru/opi/list/[0-9]+/view',parsed.path):
            raise SourceError('ДФО: не подтверждена ссылка карточки.')
        source_url='https://opi.dfo.kz'+parsed.path
        emails=[]
        for email in re.findall(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w.-]+\.[\w-]{2,}",values['e-mail']):
            email=email.strip('.,;').casefold()
            try:validate_email(email)
            except ValidationError:issues.append('Не распознан email: '+identifier);continue
            if len(email)<=254 and email not in emails:emails.append(email)
        if values['e-mail'] and not emails:issues.append('Email требует проверки: '+identifier)
        phones={}
        for label in ['телефон','мобильный']:
            text=values[label]
            found=parse_phones(text)
            # A parenthesized area code with exactly ten national digits is
            # explicit; add country code, never infer a code from city alone.
            for piece in re.split(r'[;,]',text):
                if re.match(r'^\s*\([0-9]{3,5}\)',piece):
                    digits=re.sub(r'\D','',piece)
                    if len(digits)==10:
                        phone=parse_phone('+7'+digits)
                        if phone['kind']!='unknown':found.append(phone)
            for phone in found:phones[phone['number']]=phone
            if text and not found:issues.append('Телефон требует проверки: '+identifier)
        area=values.get('область','');region=values.get('регион','');city=''
        explicit=re.search(r'г\.\s*([^,;]+)',values['адрес'])
        if explicit:city=explicit[1].strip()
        if len(city)>160:city=''
        rows.append({'identifier':identifier,'name':name,'emails':emails,'phones':list(phones.values()),
            'source_url':source_url,'profile':{'name':name,'city':city},
            'source_fields':{'dfo_address':values['адрес'],'dfo_region':{'area':area,'region':region},
                'dfo_type':values.get('особые данные',''),'dfo_legal_form':values.get('опф',''),'dfo_status':{'status':values.get('статус (дфо)',''),'block':values.get('блокировка (дфо)','')},
                'dfo_contacts':{key:values[key] for key in ['e-mail','телефон','мобильный']}}})
    if len(rows)>PAGE_SIZE or (not rows and page*PAGE_SIZE<=total):
        raise SourceError('ДФО: неполная страница; курсор сохранён.')
    if page*PAGE_SIZE<total and len(rows)!=PAGE_SIZE:
        raise SourceError('ДФО: неполная страница с продолжением.')
    if total and page<=math.ceil(total/PAGE_SIZE) and not rows:
        raise SourceError('ДФО: пустая страница; курсор сохранён.')
    return {'rows':rows,'issues':issues,'total':total,'has_next':page*PAGE_SIZE<total}


def read_page(page,transport=None):
    if not isinstance(page,int) or isinstance(page,bool) or not 1<=page<=100000:
        raise SourceError('ДФО: некорректная страница.')
    with httpx.Client(timeout=httpx.Timeout(30,connect=10),follow_redirects=False,transport=transport,
                      headers={'User-Agent':'ContactsCollector/0.1','Accept':'text/html'}) as client:
        try:
            with client.stream('GET',ENDPOINT,params={'flBlock':'Active_Свободно','presentation':'table','p':page}) as response:
                if response.status_code!=200:
                    exc=SourceError(f'ДФО: HTTP {response.status_code}. Курсор сохранён.')
                    header=response.headers.get('Retry-After','60')
                    try:delay=float(header)
                    except ValueError:
                        try:delay=(parsedate_to_datetime(header)-datetime.now(timezone.utc)).total_seconds()
                        except (ValueError,TypeError,OverflowError):delay=60
                    exc.retry_after=max(60,delay) if math.isfinite(delay) and delay<315360000 else 3600
                    raise exc
                if 'text/html' not in response.headers.get('content-type',''):raise SourceError('ДФО: ожидалась HTML-таблица.')
                raw=bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw)>4_000_000:raise SourceError('ДФО: слишком большой ответ.')
        except httpx.TransportError:raise SourceError('ДФО временно недоступен; курсор сохранён.')
    return parse_page(raw.decode('utf-8',errors='replace'),page)

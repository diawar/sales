"""Gov.kz contact-block parser; no discovery or database writes."""
import re
import phonenumbers
import httpx
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from .phones import parse_phone
from .goszakup_client import SourceError

class ContactsNotPublished(SourceError,ValueError):
    """Recognized organization contact page without a published text block."""

def canonical_url(value):
    parsed=urlsplit(value)
    if parsed.scheme!='https' or parsed.netloc!='www.gov.kz' or parsed.username or parsed.fragment:
        raise ValueError('Требуется публичная карточка https://www.gov.kz/org/…')
    match=re.fullmatch(r'/org/([a-z0-9-]{1,80})(?:/contacts)?/?',parsed.path)
    if not match or parsed.query not in ('','lang=ru'):
        raise ValueError('Требуется основная карточка организации.')
    return f'https://www.gov.kz/org/{match[1]}?lang=ru'

def parse_card(html,source_url):
    source_url=canonical_url(source_url)
    if len(html.encode('utf-8'))>4_000_000:raise ValueError('Страница слишком велика.')
    soup=BeautifulSoup(html,'html.parser')
    heading=soup.select_one('.ministry-header-top__title') or soup.select_one('h1')
    title=heading.get_text(' ',strip=True) if heading else ''
    if title.casefold()=='контакты':title=''
    blocks=soup.select('.contacts-detail, .ministry-contacts-card .contacts.inner-html')
    if title and not blocks and soup.select_one('.ministry-contacts-page .ministry-contacts-card'):
        raise ContactsNotPublished('В карточке Gov.kz текстовые контакты не опубликованы.')
    if not title or not blocks:raise ValueError('Блок контактов не распознан; пустой результат не сохраняется.')
    rows=[];emails=set();phones={};issues=[]
    for block in blocks:
        pairs=[]
        for tr in block.select('tr'):
            cells=tr.find_all(['td','th'],recursive=False)
            while len(cells)>2 and not cells[-1].get_text(' ',strip=True):cells.pop()
            if len(cells)!=2:continue
            label=cells[0].get_text(' ',strip=True)
            value=cells[1].get_text(' ',strip=True)
            if not label or not value:continue
            pairs.append((label,value))
        if not pairs:
            for paragraph in block.select('p'):
                label_node=paragraph.find(['strong','b'])
                if not label_node:continue
                label=label_node.get_text(' ',strip=True).rstrip(':').strip()
                copy=BeautifulSoup(str(paragraph),'html.parser')
                copy.find(['strong','b']).decompose()
                value=copy.get_text(' ',strip=True).lstrip(':').strip()
                if label and value:pairs.append((label,value))
        if not pairs:
            # Some contact blocks publish plain paragraphs without headings.
            # Keep their provenance; do not infer a department or address type.
            for paragraph in block.select('p'):
                value=paragraph.get_text(' ',strip=True)
                if value:pairs.append(('Контактная информация',value))
        for label,value in pairs:
            rows.append({'purpose':label,'value':value})
            for candidate in re.findall(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',value):
                try:validate_email(candidate);emails.add(candidate.lower())
                except ValidationError:issues.append('Некорректный email в блоке контактов.')
            for phone in parse_phones(value):
                key=(phone['number'],label)
                phones[key]={**phone,'purpose':label}
    if not rows:raise ValueError('Структура таблицы контактов изменилась.')
    addresses=[r['value'] for r in rows if r['purpose'].rstrip(':').casefold()=='адрес']
    city=''
    for address in addresses:
        match=re.search(r'(?:\bг\.?\s+|\bгород\s+)([А-ЯЁӘІҢҒҮҰҚӨҺ][а-яёәіңғүұқөһ-]+)',address)
        if match:city=match[1];break
    return {'name':title[:300],'source_url':source_url.replace('?','/contacts?'),'identity_key':'gov:'+urlsplit(source_url).path.split('/')[-1],
            'emails':sorted(emails),'phones':list(phones.values()),'source_rows':rows,'issues':issues,'city':city}

def parse_phones(value):
    """Expand local numbers only with an explicit area code in the same cell."""
    text=re.sub(r'\s+',' ',value)
    text=re.sub(r'\+\s+7','+7',text)
    found={}
    for match in phonenumbers.PhoneNumberMatcher(text,'KZ'):
        phone=parse_phone(match.raw_string)
        found[phone['number']]=phone
    area=re.search(r'(?:\+7|8)\s*\((\d{3,5})\)',text)
    if area:
        length=10-len(area[1])
        # Comma/semicolon-delimited continuations; no inference across cells.
        for local in re.findall(r'[,;]\s*([\d -]+)(?=[,;]|$)',text):
            digits=re.sub(r'\D','',local)
            if len(digits)==length:
                candidate='+7'+area[1]+digits;phone=parse_phone(candidate)
                if phone['kind']!='unknown':
                    phone['raw']=candidate;found[phone['number']]=phone
    return list(found.values())

def read_card(url,transport=None):
    target=canonical_url(url).replace('?','/contacts?')
    with httpx.Client(timeout=httpx.Timeout(30,connect=10),follow_redirects=False,transport=transport,headers={'User-Agent':'ContactsCollector/0.1','Accept':'text/html'}) as client:
        try:
            with client.stream('GET',target) as response:
                if response.status_code!=200:
                    exc=SourceError(f'Gov.kz: HTTP {response.status_code}. Курсор сохранён.')
                    raw=response.headers.get('Retry-After','60')
                    try:delay=float(raw)
                    except ValueError:
                        try:delay=(parsedate_to_datetime(raw)-datetime.now(timezone.utc)).total_seconds()
                        except (ValueError,TypeError,OverflowError):delay=60
                    exc.retry_after=max(60,delay) if 0<delay<315360000 else 3600
                    raise exc
                if 'text/html' not in response.headers.get('content-type',''):raise SourceError('Gov.kz вернул другой тип содержимого.')
                content=bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content)>4_000_000:raise SourceError('Страница Gov.kz слишком велика.')
            return parse_card(content.decode('utf-8'),target)
        except ContactsNotPublished:raise
        except httpx.TransportError:raise SourceError('Gov.kz временно недоступен. Прогресс сохранён.')
        except (ValueError,UnicodeDecodeError) as exc:raise SourceError(str(exc))

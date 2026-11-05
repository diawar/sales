"""Bounded reader for the public first registry tab, without authentication."""
import re
import math
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
import httpx
import phonenumbers
from django.core.validators import validate_email,URLValidator
from django.core.exceptions import ValidationError
from .phones import parse_phone
from .goszakup_client import SourceError

ENDPOINT='https://e-ondiris.gov.kz/awp-api/registry-front'
PAGE_SIZE=50

def read_page(page,transport=None):
    if not isinstance(page,int) or isinstance(page,bool) or not 1<=page<=100000:raise SourceError('Некорректная страница реестра.')
    with httpx.Client(timeout=httpx.Timeout(30,connect=10),follow_redirects=False,transport=transport,headers={'User-Agent':'ContactsPilot/0.1','Accept':'application/json'}) as client:
        try:
            with client.stream('GET',ENDPOINT,params={'page':page,'limit':PAGE_SIZE}) as response:
                if response.status_code!=200:
                    exc=SourceError(f'e-ondiris: HTTP {response.status_code}')
                    header=response.headers.get('Retry-After','60')
                    try:seconds=float(header)
                    except ValueError:
                        try:seconds=(parsedate_to_datetime(header)-datetime.now(timezone.utc)).total_seconds()
                        except (ValueError,TypeError,OverflowError):seconds=300
                    exc.retry_after=max(60,seconds) if math.isfinite(seconds) and seconds<315360000 else 3600
                    raise exc
                raw=bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw)>8*1024*1024:raise SourceError('Ответ реестра слишком большой.')
            payload=__import__('json').loads(raw)
        except httpx.TransportError:raise SourceError('e-ondiris временно недоступен; выборка сохранена.')
        except (ValueError,UnicodeDecodeError):raise SourceError('e-ondiris вернул некорректный JSON.')
    if not isinstance(payload,dict) or payload.get('success') is not True or not isinstance(payload.get('data'),list) or not isinstance(payload.get('meta'),dict):raise SourceError('Изменился формат реестра e-ondiris.')
    meta=payload['meta']
    if meta.get('page')!=page or len(payload['data'])>PAGE_SIZE or not isinstance(meta.get('hasNextPage'),bool) or not isinstance(meta.get('total'),int) or meta['total']<0:
        raise SourceError('Реестр не подтвердил страницу и пагинацию.')
    if meta['hasNextPage'] and not payload['data']:raise SourceError('Пустая страница реестра с продолжением.')
    return payload

def normalize(row):
    if not isinstance(row,dict):raise ValueError('Некорректная строка.')
    identifier=row.get('bin_iin');name=row.get('company_name')
    if not isinstance(identifier,str) or not re.fullmatch(r'[0-9]{12}',identifier) or not isinstance(name,str) or not name.strip() or len(name)>300:raise ValueError('Не подтверждены идентификатор и название организации.')
    if row.get('is_active') is not None and not isinstance(row['is_active'],bool):raise ValueError('Изменился формат активности записи.')
    if row.get('is_active') is False:return None,[]
    issues=[];emails=[];phones=[];websites=[]
    for key in ['email','phone','website','oked_codes','oked_names','region_kato','production_address']:
        if row.get(key) is not None and not isinstance(row[key],str):raise ValueError(f'Изменился формат поля {key}.')
    email_text=row.get('email') or ''
    for email in re.findall(r'[\w.!#$%&\x27*+/=?^`{|}~-]+@[\w.-]+\.[\w-]{2,}',email_text):
        email=email.strip('.,;').casefold()
        try:validate_email(email)
        except ValidationError:
            issues.append('Некорректный email.');continue
        if len(email)<=254 and email not in emails:emails.append(email)
    if email_text.strip() and not emails:issues.append('Email опубликован, но не распознан.')
    phone_text=row.get('phone') or ''
    matches=list(phonenumbers.PhoneNumberMatcher(phone_text,'KZ'))
    raw_phones=[match.raw_string for match in matches] if matches else [p.strip() for p in re.split(r'[;|\n]',phone_text) if p.strip()]
    for raw in raw_phones:
        if len(raw)>200:
            issues.append('Длинное поле телефона требует проверки.');continue
        parsed=parse_phone(raw)
        if parsed not in phones:phones.append(parsed)
    for website in re.split(r'[;|\s]+',row.get('website') or ''):
        if not website:continue
        candidate=website if '://' in website else 'https://'+website
        try:
            URLValidator(schemes=['https','http'])(candidate)
            if len(candidate)>1000:raise ValidationError('Длинная ссылка')
        except ValidationError:
            issues.append('Не распознан сайт.');continue
        if candidate not in websites:websites.append(candidate)
    region=(row.get('region_kato') or '').strip()
    city=''
    city_match=re.fullmatch(r'(?:г\.|город)\s*([\wӘәҒғҚқҢңӨөҰұҮүҺһІіЁё -]{1,100})',region,re.I)
    if city_match:city=city_match.group(1).strip()
    industry=(row.get('oked_names') or '').strip()
    code=(row.get('oked_codes') or '').strip()
    if len(industry)>200:
        issues.append('Описание деятельности длиннее поля; сохранено в данных источника.');industry='ОКЭД '+code if code else ''
    if not industry and code:industry='ОКЭД '+code
    profile={'name':name.strip()}
    if city:profile['city']=city
    if industry:profile['industry']=industry[:200]
    if websites:profile['websites']=websites
    return {'identifier':identifier,'name':name.strip(),'emails':emails,'phones':phones,'profile':profile,'source_url':'https://e-ondiris.gov.kz/?bin='+identifier,'source_fields':{k:row.get(k) or '' for k in ['region_kato','production_address','oked_codes','oked_names']},'source_key':str(row.get('registration_number') or '')[:100]},issues

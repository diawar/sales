"""Offline research parser; no network and no database writes."""
import re
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from django.core.validators import validate_email,URLValidator
from django.core.exceptions import ValidationError
from .phones import parse_phone

def parse_vspravke(html,url):
    if not re.fullmatch(r'https://kz\.vspravke\.com/[a-z-]+/company/[0-9]+',url):raise ValueError('Некорректная ссылка карточки.')
    soup=BeautifulSoup(html,'html.parser')
    block=soup.find(attrs={'itemtype':re.compile(r'^https?://schema\.org/Organization$')})
    if block is None:raise ValueError('Не распознан блок организации.')
    heading=block.find('h1',itemprop='name')
    if not heading or not heading.get_text(strip=True):raise ValueError('Отсутствует название организации.')
    def values(prop):
        return list(dict.fromkeys((x.get('content') or x.get_text(' ',strip=True)).strip() for x in block.find_all(attrs={'itemprop':prop}) if x.get('content') or x.get_text(strip=True)))
    emails=[];websites=[];phones={}
    email_values=values('email')+re.findall(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',block.get_text(' ',strip=True))
    for text in email_values:
        email=text.removeprefix('mailto:').strip().lower()
        try:validate_email(email)
        except ValidationError:continue
        if email not in emails:emails.append(email)
    for text in values('url'):
        if not text.startswith(('https://','http://')):continue
        try:URLValidator(schemes=['http','https'])(text)
        except ValidationError:continue
        if urlsplit(text).hostname!='kz.vspravke.com' and text not in websites:websites.append(text)
    for text in values('telephone'):
        for raw in re.split(r'[,;|]',text):
            phone=parse_phone(raw.strip())
            if phone['kind']!='unknown':phones[phone['number']]=phone
    return {'url':url,'source_id':url.rsplit('/',1)[-1],'name':heading.get_text(' ',strip=True),'city':next(iter(values('addressLocality')),''),'addresses':values('streetAddress'),'emails':emails,'phones':list(phones.values()),'websites':websites}

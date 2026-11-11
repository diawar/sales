"""Parse public merchant contact blocks observed in the browser. No crawler."""
import re
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from .phones import parse_phone
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

def parse_seller_html(html,source_url=''):
    merchant_id=None
    if source_url:
        parts=urlsplit(source_url)
        if parts.scheme!='https' or parts.netloc not in {'kaspi.kz','www.kaspi.kz'}:
            raise ValueError('Нужна публичная HTTPS-ссылка Kaspi.')
        match=re.search(r'^/shop/(?:info/merchant|m)/([A-Za-z0-9_-]+)(?:/|$)',parts.path)
        if match:merchant_id=match.group(1)
    soup=BeautifulSoup(html,'html.parser')
    title=soup.select_one('.merchant-shop-profile__title,.merchant-profile__title')
    title_text=' '.join(title.get_text(' ',strip=True).split()) if title else ''
    name,view_city=title_text,''
    if ' в городе ' in title_text:
        name,view_city=title_text.rsplit(' в городе ',1)
    phones=[];addresses=[]
    for row in soup.select('.merchant-contacts-informer .merchant-contacts-informer__row'):
        text=row.select_one('.merchant-contacts-informer__text')
        if text is None:continue
        value=' '.join(text.get_text(' ',strip=True).split())
        if row.select_one('[data-test-id="icon-phone"]'):
            number=parse_phone(value)
            if number not in phones:phones.append(number)
        elif row.select_one('[data-test-id="icon-pin"]'):
            if value and value not in addresses:addresses.append(value)
    cities=[]
    for address in addresses:
        match=re.search(r'(?:^|[,;])\s*г\.\s*([^,;]+)',address,re.I)
        if match and match.group(1).strip() not in cities:cities.append(match.group(1).strip())
    emails=[]
    for link in soup.select('.merchant-contacts-informer a[href^="mailto:"]'):
        email=link.get('href','')[7:].split('?',1)[0].strip().lower()
        try:validate_email(email)
        except ValidationError:continue
        if email not in emails:emails.append(email)
    return {'merchant_id':merchant_id,'shop_name':name,'city':cities[0] if len(cities)==1 else '',
            'view_city':view_city,'address_cities':cities,'phones':phones,'addresses':addresses,
            'source_url':source_url,'email':emails[0] if emails else None,'emails':emails}

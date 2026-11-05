"""Offline proof of parsing user-provided public seller HTML. No crawler."""
import re
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from .phones import parse_phone

def parse_seller_html(html,source_url=''):
    merchant_id=None
    if source_url:
        parts=urlsplit(source_url)
        if parts.scheme!='https' or parts.netloc not in {'kaspi.kz','www.kaspi.kz'}:
            raise ValueError('Нужна публичная HTTPS-ссылка Kaspi.')
        match=re.search(r'/shop/info/merchant/(\d+)(?:/|$)',parts.path)
        if match:merchant_id=match.group(1)
    soup=BeautifulSoup(html,'html.parser')
    title=soup.select_one('.merchant-shop-profile__title')
    title_text=' '.join(title.get_text(' ',strip=True).split()) if title else ''
    name,city=title_text,''
    if ' в городе ' in title_text:
        name,city=title_text.rsplit(' в городе ',1)
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
    return {'merchant_id':merchant_id,'shop_name':name,'city':city,'phones':phones,'addresses':addresses,'source_url':source_url,'email':None}

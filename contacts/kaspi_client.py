"""Read-only public catalogue requests used by the Kaspi storefront; no user session."""
import re
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit,urlencode
import httpx
from .phones import parse_phone

CITY='750000000'
CATEGORIES=[
    {'code':'cpus','name':'Процессоры'}, {'code':'desktops','name':'Системные блоки'},
    {'code':'videocards','name':'Видеокарты'}, {'code':'ram','name':'Оперативная память'},
    {'code':'motherboards','name':'Материнские платы'}, {'code':'hard drives','name':'Накопители'},
    {'code':'power supplies','name':'Блоки питания'}, {'code':'fans and cooling','name':'Охлаждение'},
    {'code':'computer cases','name':'Корпуса'}, {'code':'other hardware','name':'Другие комплектующие'},
]
CATEGORY_MAP={r['code']:r['name'] for r in CATEGORIES}

class KaspiError(ValueError):pass
class KaspiBlocked(KaspiError):pass
class KaspiFormat(KaspiError):pass
class KaspiRetry(KaspiError):
    def __init__(self,message,delay=30):super().__init__(message);self.delay=delay

def identifier(value,numeric=False):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]{1,20}' if numeric else r'[A-Za-z0-9_-]{1,80}',value):
        raise KaspiFormat('Kaspi: неверный ID в ответе.')
    return value

def product_url(value,product_id):
    if not isinstance(value,str):raise KaspiFormat('Kaspi: отсутствует ссылка на товар.')
    value='https://kaspi.kz/shop'+value if value.startswith('/p/') else value
    if value.startswith('/shop/p/'):value='https://kaspi.kz'+value
    parts=urlsplit(value)
    if parts.scheme!='https' or parts.netloc!='kaspi.kz' or not parts.path.startswith('/shop/p/') or not parts.path.rstrip('/').endswith('-'+product_id):
        raise KaspiFormat('Kaspi: неверная ссылка на товар.')
    return 'https://kaspi.kz'+parts.path

class KaspiClient:
    def __init__(self,transport=None):
        self.http=httpx.Client(timeout=25,follow_redirects=False,transport=transport,
            headers={'User-Agent':'SalesContactCollector/0.1','Accept':'application/json','Origin':'https://kaspi.kz'})
    def close(self):self.http.close()
    def request(self,path,referer,*,params=None,payload=None):
        try:
            if payload is None:r=self.http.get('https://kaspi.kz'+path,params=params,headers={'Referer':referer})
            else:r=self.http.post('https://kaspi.kz'+path,json=payload,headers={'Referer':referer})
        except httpx.RequestError:raise KaspiRetry('Kaspi: временная ошибка соединения.')
        if r.status_code in {401,403}:raise KaspiBlocked('Kaspi ограничил доступ. Сбор остановлен; очередь сохранена.')
        if r.status_code==429:
            delay=300
            retry=r.headers.get('Retry-After','')
            if retry.isdigit():delay=max(10,int(retry))
            elif retry:
                try:
                    date=parsedate_to_datetime(retry)
                    if date.tzinfo is None:date=date.replace(tzinfo=timezone.utc)
                    delay=max(10,int((date-datetime.now(timezone.utc)).total_seconds())+1)
                except (ValueError,TypeError,OverflowError):pass
            if delay>30*86400:raise KaspiBlocked('Kaspi запросил длительную паузу. Очередь сохранена для ручной проверки.')
            raise KaspiRetry('Kaspi: ограничение частоты, ожидание перед повтором.',delay)
        if r.status_code>=500:raise KaspiRetry('Kaspi: временная ошибка сервера.')
        if r.status_code!=200:raise KaspiFormat(f'Kaspi: неожиданный HTTP {r.status_code}.')
        if len(r.content)>5_000_000:raise KaspiFormat('Kaspi: слишком большой ответ.')
        try:data=r.json()
        except ValueError:raise KaspiFormat('Kaspi: вместо JSON получен неизвестный ответ; требуется проверка.')
        if not isinstance(data,dict):raise KaspiFormat('Kaspi: неизвестный формат ответа.')
        return data
    def catalogue(self,code,page,city):
        if code not in CATEGORY_MAP:raise KaspiFormat('Категория не согласована.')
        identifier(city,True)
        data=self.request('/yml/product-view/pl/results','https://kaspi.kz/shop/c/'+code.replace(' ','%20')+'/?c='+city,
                          params={'q':':category:'+code,'c':city,'page':page,'sort':'relevance'})
        if not isinstance(data.get('data'),list):raise KaspiFormat('Kaspi: в каталоге нет списка data.')
        rows=[]
        for item in data['data']:
            if not isinstance(item,dict):raise KaspiFormat('Kaspi: повреждённая карточка каталога.')
            pid=identifier(item.get('id'),True)
            norm=lambda v:re.sub(r'[^a-z0-9]','',str(v).casefold())
            expected={norm(code)}|({'desktopcomputers'} if code=='desktops' else set())
            if not isinstance(item.get('categoryCodes'),list) or not expected.intersection({norm(v) for v in item['categoryCodes']}):
                raise KaspiFormat('Kaspi вернул товары другой категории; сбор остановлен для проверки.')
            rows.append({'id':pid,'url':product_url(item.get('shopLink'),pid)})
        return rows
    def offers(self,pid,url,page,city):
        identifier(pid,True);identifier(city,True);product_url(url,pid)
        data=self.request('/yml/offer-view/offers/'+pid,url,
                          payload={'cityId':city,'id':pid,'page':page,'limit':5,'sort':True})
        total=data.get('total');offers=data.get('offers')
        if type(total)!=int or total<0 or not isinstance(offers,list):raise KaspiFormat('Kaspi: неизвестная структура предложений.')
        if len(offers)>5 or len(offers)<min(5,max(0,total-page*5)):
            raise KaspiFormat('Kaspi: неполная страница предложений; требуется проверка пагинации.')
        result=[]
        for offer in offers:
            if not isinstance(offer,dict):raise KaspiFormat('Kaspi: повреждено предложение.')
            mid=identifier(offer.get('merchantId'))
            name=offer.get('merchantName')
            if not isinstance(name,str) or not name.strip():raise KaspiFormat('Kaspi: отсутствует имя магазина.')
            result.append({'id':mid,'name':name.strip()[:300]})
        if total and not result:raise KaspiFormat('Kaspi: пустая страница при ненулевом количестве продавцов.')
        return {'rows':result,'total':total}
    def details(self,mid):
        identifier(mid)
        data=self.request('/yml/merchant-info/p/'+mid+'/details','https://kaspi.kz/shop/m/'+mid+'/products/')
        if data.get('uid')!=mid or not isinstance(data.get('name'),str) or not data['name'].strip():raise KaspiFormat('Kaspi: ID или имя карточки магазина не совпадает.')
        address=data.get('legalAddress') or ''
        if not isinstance(address,str):raise KaspiFormat('Kaspi: неизвестный формат адреса.')
        return {'merchant_id':mid,'shop_name':data['name'].strip()[:300],'addresses':[address] if address else []}
    def support(self,mid,city):
        identifier(mid);identifier(city,True)
        data=self.request('/yml/merchant-info/p/'+mid+'/support','https://kaspi.kz/shop/m/'+mid+'/products/',params={'c':city})
        if 'phone' not in data and 'formattedPhone' not in data:raise KaspiFormat('Kaspi: неизвестная структура публичных контактов.')
        raw=data.get('formattedPhone') or data.get('phone') or ''
        if not isinstance(raw,str) or len(raw)>200:raise KaspiFormat('Kaspi: неизвестный формат телефона.')
        return [parse_phone(raw)] if raw else []

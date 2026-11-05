"""Only the public endpoints needed to discover business contacts."""
import json
import time
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
from dataclasses import dataclass
from urllib.parse import urlsplit,urljoin
import httpx

class SourceError(RuntimeError):
    pass

class NotFound(SourceError):
    pass

@dataclass
class Page:
    count:int
    results:list
    next_url:str|None

def utc_datetime(value):
    if not value:
        return None
    try:
        date=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    except ValueError:
        raise SourceError('Некорректная дата источника.')
    if date.tzinfo is None:
        raise SourceError('Дата источника без часового пояса.')
    return date.astimezone(timezone.utc)

class PublicClient:
    def __init__(self,pace=None,transport=None):
        self.http=httpx.Client(base_url='https://zakup.gov.kz',timeout=httpx.Timeout(30,connect=10),follow_redirects=False,transport=transport,headers={'User-Agent':'ContactsCollector/0.1','Accept':'application/json'})
        self.attempts=1
        self.sleep=time.sleep
        self.pace=pace
        self.last=0
    def __enter__(self):return self
    def __exit__(self,*args):self.http.close()
    def _pace(self):
        if self.pace:
            self.pace()
        else:
            time.sleep(max(0,1-(time.monotonic()-self.last)))
            self.last=time.monotonic()
    def _delay(self,response,attempt):
        raw=response.headers.get('Retry-After','60')
        try:return max(1,float(raw))
        except ValueError:
            try:return max(1,(parsedate_to_datetime(raw)-datetime.now(timezone.utc)).total_seconds())
            except (TypeError,ValueError):return 60
    def get_json(self,path,params=None):
        url=urljoin('https://zakup.gov.kz',path)
        parts=urlsplit(url)
        if parts.scheme!='https' or parts.netloc!='zakup.gov.kz' or parts.path not in {'/api/core/api/public/announcements/','/api/core/api/public/organizations/'} or parts.fragment:
            raise SourceError('Недопустимый адрес API.')
        self._pace()
        try:
            with self.http.stream('GET',url,params=params) as response:
                if response.status_code!=200:
                    exc=SourceError(f'Источник API: HTTP {response.status_code}')
                    exc.retry_after=self._delay(response,0) if response.status_code in (429,503) else 60
                    raise exc
                data=bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data)>8*1024*1024:raise SourceError('Ответ API превышает допустимый размер.')
            return json.loads(data)
        except httpx.TransportError:
            raise SourceError('Сетевая ошибка публичного API; прогресс сохранён.')
        except (ValueError,UnicodeDecodeError):
            raise SourceError('Изменился JSON-формат API.')
    def search(self,resource='announcements',**params):
        if resource!='announcements':raise SourceError('Неизвестный ресурс источника.')
        value=self.get_json('/api/core/api/public/announcements/',params)
        if not isinstance(value,dict) or not isinstance(value.get('results'),list) or isinstance(value.get('count'),bool) or not isinstance(value.get('count'),int) or value['count']<0:
            raise SourceError('Изменился формат списка итогов.')
        if any(not isinstance(row,dict) for row in value['results']):raise SourceError('Некорректные строки итогов.')
        return Page(value['count'],value['results'],value.get('next'))

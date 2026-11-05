"""Bounded public contact-page research. No client DB imports or mail probes."""
import json,re,time,hashlib,ipaddress,socket,sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from urllib.parse import urlsplit,urljoin,unquote
from urllib.robotparser import RobotFileParser
from bs4 import BeautifulSoup
import httpx

ROOT=Path('data/company-site-review');ROOT.mkdir(parents=True,exist_ok=True)
AGENT='GPTBot'
EMAIL=re.compile(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')

def public(url,host):
    p=urlsplit(url)
    if p.scheme not in ('https','http') or p.username or p.password or p.port not in (None,80,443):return False
    if (p.hostname or '').removeprefix('www.')!=host:return False
    try:return all(ipaddress.ip_address(a[4][0]).is_global for a in socket.getaddrinfo(p.hostname,None))
    except (socket.gaierror,ValueError):return False

def review(seed):
    initial=seed['websites'][0];host=urlsplit(initial).hostname.removeprefix('www.')
    folder=ROOT/seed['source_id'];folder.mkdir(exist_ok=True)
    result={'source_id':seed['source_id'],'name':seed['name'],'directory_url':seed['url'],'website':initial,'pages':[],'emails':[],'notes':[]}
    robots={};last=0;count=0
    with httpx.Client(timeout=httpx.Timeout(10,connect=5),follow_redirects=False,headers={'User-Agent':'GPTBot (bounded public business contact research)','Accept':'text/html,text/plain;q=0.9'}) as client:
        def get(url):
            nonlocal last,count
            if not public(url,host):raise ValueError('Внешний или непубличный адрес: переход не выполнен.')
            time.sleep(max(0,1.2-(time.monotonic()-last)));last=time.monotonic();count+=1
            if count>14:raise ValueError('Достигнут предел запросов сайта.')
            with client.stream('GET',url) as response:
                raw=bytearray()
                if response.status_code==200:
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw)>2_000_000:raise ValueError('Ответ больше 2 МБ.')
                return response.status_code,response.headers,bytes(raw)
        def allowed(url):
            p=urlsplit(url);origin=f'{p.scheme}://{p.netloc}'
            if origin not in robots:
                robots_url=origin+'/robots.txt'
                for _ in range(4):
                    status,headers,raw=get(robots_url)
                    if status not in (301,302,303,307,308):break
                    target=urljoin(robots_url,headers.get('location',''))
                    if not headers.get('location') or not public(target,host):raise ValueError('Редирект robots за пределы домена; обход остановлен.')
                    robots_url=target
                if status==404:robots[origin]=None
                elif status==200 and not raw.lstrip().lower().startswith((b'<!doctype',b'<html')):
                    text=raw.decode('utf-8',errors='replace');folder.joinpath(hashlib.sha256(origin.encode()).hexdigest()[:8]+'-robots.txt').write_text(text,encoding='utf-8')
                    parser=RobotFileParser();parser.parse(text.splitlines());robots[origin]=parser
                else:raise ValueError(f'Правила robots не подтверждены (HTTP {status}); обход остановлен.')
            return robots[origin] is None or robots[origin].can_fetch(AGENT,url)
        def page(url):
            for _ in range(4):
                if not allowed(url):raise ValueError('Путь закрыт в robots для GPTBot.')
                status,headers,raw=get(url)
                if status in (301,302,303,307,308):
                    next_url=urljoin(url,headers.get('location',''))
                    if not headers.get('location') or next_url==url:raise ValueError('Некорректный редирект.')
                    if not public(next_url,host):raise ValueError('Редирект на другой домен; принадлежность не подтверждена.')
                    url=next_url;continue
                if status!=200:raise ValueError(f'HTTP {status}')
                if 'text/html' not in headers.get('content-type',''):raise ValueError('Страница не HTML.')
                text=raw.decode('utf-8',errors='replace')
                if text.count('\ufffd')>10:text=raw.decode('windows-1251',errors='replace')
                filename=hashlib.sha256(url.encode()).hexdigest()[:12]+'.html';folder.joinpath(filename).write_text(text,encoding='utf-8')
                return url,text,filename
            raise ValueError('Слишком много перенаправлений.')
        try:
            final,html,filename=page(initial)
            queue=[(final,html,filename)]
            soup=BeautifulSoup(html,'html.parser');links=[]
            for a in soup.select('a[href]'):
                href=a['href'];label=a.get_text(' ',strip=True)
                target=urljoin(final,href).split('#')[0]
                if re.search(r'контакт|contacts?|contact-us',label+' '+urlsplit(target).path,re.I) and public(target,host) and target!=final and not urlsplit(target).query and not re.search(r'\.(pdf|zip|docx?)$',target,re.I):
                    if target not in links:links.append(target)
            for target in links[:2]:
                try:queue.append(page(target))
                except Exception as exc:result['notes'].append(f'{target}: {type(exc).__name__}: {str(exc)[:180]}')
            for url,html,filename in queue:
                soup=BeautifulSoup(html,'html.parser')
                for node in soup.select('script,style,noscript,form'):node.decompose()
                emails=set(EMAIL.findall(soup.get_text(' ',strip=True)))
                for a in soup.select('a[href]'):
                    if a['href'].lower().startswith('mailto:'):emails.update(EMAIL.findall(unquote(a['href'].split('?',1)[0][7:])))
                emails={e.lower().strip('.,;') for e in emails if len(e)<=254 and not re.search(r'\.(png|jpg|svg|webp)$',e,re.I)}
                result['pages'].append({'url':url,'file':str(folder/filename),'emails':sorted(emails),'title':soup.title.get_text(' ',strip=True)[:200] if soup.title else ''})
                for email in sorted(emails):result['emails'].append({'email':email,'url':url,'status':'Опубликован на сайте; принадлежность и доставка не проверены'})
        except Exception as exc:result['error']=f'{type(exc).__name__}: {str(exc)[:200]}'
    folder.joinpath('result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result

if __name__=='__main__':
    rows=json.loads(Path('data/directory-review/parsed-sample.json').read_text(encoding='utf-8'))
    seeds=[row for row in rows if row['websites']]
    results=[]
    if '--resume-redirects' in sys.argv:
        previous=json.loads(ROOT.joinpath('results.json').read_text(encoding='utf-8'))
        retry={r['source_id'] for r in previous if 'HTTP 301' in r.get('error','')}
        results=[r for r in previous if r['source_id'] not in retry]
        seeds=[row for row in seeds if row['source_id'] in retry]
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending={pool.submit(review,row):row['source_id'] for row in seeds}
        for future in as_completed(pending):
            try:result=future.result()
            except Exception as exc:result={'source_id':pending[future],'error':type(exc).__name__}
            results.append(result)
            ROOT.joinpath('results.json').write_text(json.dumps(sorted(results,key=lambda r:r['source_id']),ensure_ascii=False,indent=2),encoding='utf-8')
            print(result['source_id'],'pages',len(result.get('pages',[])),'emails',len({e['email'] for e in result.get('emails',[])}),result.get('error',''),flush=True)

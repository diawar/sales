"""Bounded read-only public directory sample. Never imports clients."""
from pathlib import Path
import json,time,sys,re
from urllib.parse import urlsplit,urljoin
import httpx
from bs4 import BeautifulSoup

ROOT=Path('data/directory-review');ROOT.mkdir(parents=True,exist_ok=True)
URLS={'spr':'https://kz.spr.ru/almati/','vspravke':'https://kz.vspravke.com/almaty','allbiz':'https://kz.all.biz/'}

def fetch(url,name):
    with httpx.Client(timeout=25,follow_redirects=False,headers={'User-Agent':'ContactsResearch/0.1'}) as client:
        with client.stream('GET',url) as response:
            status=response.status_code;location=response.headers.get('location','')
            raw=bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw)>4_000_000:raise ValueError('Response too large')
            encoding=response.encoding or 'utf-8'
    text=bytes(raw).decode(encoding,errors='replace')
    if text.count('\ufffd')>10:
        text=bytes(raw).decode('windows-1251',errors='replace')
    ROOT.joinpath(name+'.html').write_text(text,encoding='utf-8')
    return text,{'url':url,'status':status,'location':location,'bytes':len(raw)}

def inspect():
    for source,url in URLS.items():
        base='https://'+urlsplit(url).netloc
        for path,label in [('/robots.txt','robots'),(urlsplit(url).path,'landing')]:
            try:
                html,meta=fetch(base+path,source+'-'+label)
                soup=BeautifulSoup(html,'html.parser')
                meta['links']=[{'text':a.get_text(' ',strip=True)[:90],'url':urljoin(base+path,a['href'])} for a in soup.select('a[href]') if a.get_text(' ',strip=True)]
                ROOT.joinpath(source+'-'+label+'.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
                print(source,label,meta['status'],meta['bytes'])
            except Exception as exc:print(source,label,type(exc).__name__)
            time.sleep(1)

def categories(listings=False):
    targets={'vspravke-computers':'https://kz.vspravke.com/almaty/rubric/7','vspravke-business':'https://kz.vspravke.com/almaty/rubric/27','spr-computers':'https://kz.spr.ru/almati/magazini-kompyuterov-i-kompyuternoy-tehniki/'} if not listings else {'vspravke-list-251':'https://kz.vspravke.com/almaty/category/251','vspravke-list-247':'https://kz.vspravke.com/almaty/category/247'}
    for name,url in targets.items():
        try:
            html,meta=fetch(url,name);soup=BeautifulSoup(html,'html.parser')
            meta['links']=[{'text':a.get_text(' ',strip=True)[:120],'url':urljoin(url,a['href'])} for a in soup.select('a[href]') if a.get_text(' ',strip=True)]
            ROOT.joinpath(name+'.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
            print(name,meta['status'],meta['bytes'],flush=True)
        except Exception as exc:print(name,repr(exc),flush=True)
        time.sleep(2)

def sample():
    selected={}
    for category in ('251','247'):
        listing=json.loads(ROOT.joinpath('vspravke-list-'+category+'.json').read_text(encoding='utf-8'))
        urls=list(dict.fromkeys(a['url'].split('#')[0] for a in listing['links'] if re.fullmatch(r'https://kz\.vspravke\.com/almaty/company/[0-9]+',a['url'])))[:10]
        for url in urls:selected.setdefault(url,[]).append(category)
    manifest=[]
    for url,categories_found in selected.items():
        name='vspravke-card-'+url.rsplit('/',1)[-1]
        try:
            _,meta=fetch(url,name);meta.update({'file':name+'.html','categories':categories_found});manifest.append(meta)
            print(name,meta['status'],flush=True)
        except Exception as exc:
            manifest.append({'url':url,'categories':categories_found,'error':type(exc).__name__});print(name,type(exc).__name__,flush=True)
        ROOT.joinpath('sample-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        time.sleep(2)

if __name__=='__main__':
    if '--sample' in sys.argv:sample()
    elif '--listings' in sys.argv:categories(True)
    elif '--categories' in sys.argv:categories()
    else:inspect()

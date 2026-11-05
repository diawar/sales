"""Inspect public Gov.kz homepage for its published organization directory."""
from pathlib import Path
import json
import re
import httpx
from bs4 import BeautifulSoup

response=httpx.get('https://www.gov.kz/?lang=ru',timeout=30,follow_redirects=False)
response.raise_for_status()
if len(response.content)>8_000_000:raise ValueError('Too large')
Path('data/gov-inspection/catalog-home.html').write_text(response.text,encoding='utf-8')
soup=BeautifulSoup(response.text,'html.parser')
print('links',json.dumps([{'text':a.get_text(' ',strip=True),'href':a.get('href')} for a in soup.select('a[href]') if any(s in a.get('href','') for s in ['/org/','entities','gosudar'])],ensure_ascii=True))
print('scripts',json.dumps([{'src':s.get('src'),'id':s.get('id'),'length':len(s.get_text()),'sample':s.get_text()[:1000]} for s in soup.select('script')],ensure_ascii=True))
if '--assets' in __import__('sys').argv:
    paths=list(dict.fromkeys(re.findall(r'/static/js/(?:public\.page|root|public)\.[A-Za-z0-9_-]+\.js',response.text)))
    for path in paths:
        asset=httpx.get('https://www.gov.kz'+path,timeout=30)
        asset.raise_for_status()
        if len(asset.content)>4_000_000:raise ValueError('Asset too large')
        Path('data/gov-inspection/'+path.rsplit('/',1)[1]).write_text(asset.text,encoding='utf-8')
        print('ASSET',path)
        for match in list(re.finditer(r'organizations|entities|/api/|getOrganizations|orgList',asset.text))[:30]:
            print(json.dumps(asset.text[max(0,match.start()-150):match.end()+250],ensure_ascii=True))

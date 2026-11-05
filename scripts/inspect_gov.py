"""Bounded public HTML inspection; does not write client records."""
from pathlib import Path
import httpx
from bs4 import BeautifulSoup

folder=Path('data/gov-inspection');folder.mkdir(parents=True,exist_ok=True)
with httpx.Client(timeout=30,follow_redirects=False) as client:
    for slug in ('astana/contacts','kazyna/contacts','edu/contacts'):
        response=client.get(f'https://www.gov.kz/org/{slug}?lang=ru')
        print(slug,response.status_code,response.headers.get('location',''))
        if response.status_code!=200:continue
        if len(response.content)>4_000_000:raise ValueError('Page too large')
        folder.joinpath(slug.replace('/','-')+'.html').write_text(response.text,encoding='utf-8')
        soup=BeautifulSoup(response.text,'html.parser')
        print('TITLE',soup.title.get_text(' ',strip=True) if soup.title else '')
        print('TABLES',len(soup.select('table')),'DETAIL',len(soup.select('.contacts-detail')))

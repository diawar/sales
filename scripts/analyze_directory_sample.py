"""Offline analysis of fetched public HTML, without importing clients."""
import json,re,sys,os
from pathlib import Path
from collections import Counter
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
ROOT=Path('data/directory-review')

def analyze():
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE','mailer.settings')
    from contacts.directory_sample import parse_vspravke
    manifest=json.loads(ROOT.joinpath('sample-manifest.json').read_text(encoding='utf-8'))
    rows=[];errors=[]
    for item in manifest:
        if item.get('status')!=200:
            errors.append({'url':item['url'],'error':item.get('error',str(item.get('status')))});continue
        try:
            row=parse_vspravke(ROOT.joinpath(item['file']).read_text(encoding='utf-8'),item['url']);row['discovered_categories']=item['categories'];rows.append(row)
        except ValueError as exc:errors.append({'url':item['url'],'error':str(exc)})
    phones=Counter(p['number'] for r in rows for p in r['phones'])
    domains=Counter(urlsplit(w).hostname.removeprefix('www.') for r in rows for w in r['websites'])
    names=Counter(r['name'].casefold() for r in rows)
    summary={'attempted':len(manifest),'parsed':len(rows),'errors':errors,'with_email':sum(bool(r['emails']) for r in rows),'with_phone':sum(bool(r['phones']) for r in rows),'with_website':sum(bool(r['websites']) for r in rows),'with_city':sum(bool(r['city']) for r in rows),'with_address':sum(bool(r['addresses']) for r in rows),'unique_emails':len({e for r in rows for e in r['emails']}),'unique_phones':len(phones),'phone_kinds':dict(Counter(p['kind'] for r in rows for p in r['phones'])),'repeated_names':{k:v for k,v in names.items() if v>1},'repeated_phones':{k:v for k,v in phones.items() if v>1},'repeated_site_domains':{k:v for k,v in domains.items() if v>1}}
    ROOT.joinpath('parsed-sample.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    ROOT.joinpath('sample-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=True,indent=2))

def inspect():
    soup=BeautifulSoup(ROOT.joinpath('vspravke-card-9191.html').read_text(encoding='utf-8'),'html.parser')
    result={'schema':[t.get_text()[:3000] for t in soup.find_all('script',type='application/ld+json')],
            'h1':[x.get_text(' ',strip=True) for x in soup.select('h1')],
            'props':[(x.name,x.get('itemprop'),x.get('content'),x.get_text(' ',strip=True)[:180]) for x in soup.select('[itemprop]')],
            'contact_links':[(x.get('href'),x.get_text(' ',strip=True)) for x in soup.select('a[href]') if any(p in x.get('href','') for p in ('mailto:','tel:','/go/','/site/'))],
            'text':soup.get_text(' ',strip=True)[-5500:]}
    print(json.dumps(result,ensure_ascii=True,indent=2))

if __name__=='__main__':analyze() if '--analyze' in sys.argv else inspect()

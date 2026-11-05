"""Read three public DFO table pages; report coverage without importing clients."""
import json
import re
import time
from pathlib import Path
import httpx
from bs4 import BeautifulSoup


def parse_table(html):
    soup=BeautifulSoup(html,'html.parser')
    for table in soup.select('table'):
        headers=[cell.get_text(' ',strip=True).casefold() for cell in table.select('thead th')]
        if 'e-mail' not in headers:
            continue
        rows=[]
        for tr in table.select('tbody tr'):
            values=[td.get_text(' ',strip=True) for td in tr.find_all('td',recursive=False)]
            if len(values)!=len(headers):continue
            entry=dict(zip(headers,values))
            if not re.fullmatch(r'\d{12}',entry.get('бин','')):continue
            rows.append(entry)
        return rows
    raise ValueError('Expected public organization table not found')


if __name__=='__main__':
    folder=Path('data/dfo-inspection');folder.mkdir(parents=True,exist_ok=True)
    output=[];all_rows=[]
    with httpx.Client(timeout=30,follow_redirects=False) as client:
        for page in (1,2,3):
            response=client.get('https://opi.dfo.kz/ru/opi/list',params={
                'flBlock':'Active_Свободно','presentation':'table','p':page})
            response.raise_for_status()
            if len(response.content)>4_000_000:raise ValueError('Page too large')
            folder.joinpath(f'page-{page}.html').write_text(response.text,encoding='utf-8')
            rows=parse_table(response.text);all_rows.extend(rows)
            totals=re.findall(r'Всего записей\s*([\d\s\xa0]+)',BeautifulSoup(response.text,'html.parser').get_text(' ',strip=True))
            output.append({'page':page,'status':response.status_code,'rows':len(rows),
                'with_email':sum(bool(r.get('e-mail')) for r in rows),
                'with_phone':sum(bool(r.get('телефон') or r.get('мобильный')) for r in rows),
                'catalog_total':re.sub(r'\s','',totals[0]) if totals else None})
            print(json.dumps(output[-1],ensure_ascii=True),flush=True)
            time.sleep(2)
    unique={r['бин']:r for r in all_rows}
    report={'pages':output,'unique_bins':len(unique),
        'with_email':sum(bool(r.get('e-mail')) for r in unique.values()),
        'with_phone':sum(bool(r.get('телефон') or r.get('мобильный')) for r in unique.values())}
    folder.joinpath('rows.json').write_text(json.dumps(list(unique.values()),ensure_ascii=False),encoding='utf-8')
    folder.joinpath('summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)

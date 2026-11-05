"""Offline findings and evidence, never client database writes."""
import json,re
from pathlib import Path
from collections import Counter
from bs4 import BeautifulSoup
import phonenumbers
ROOT=Path('data/company-site-review')
rows=json.loads(ROOT.joinpath('results.json').read_text(encoding='utf-8'))
seeds={r['source_id']:r for r in json.loads(Path('data/directory-review/parsed-sample.json').read_text(encoding='utf-8'))}
for row in rows:
    contexts={};numbers=set();titles=[]
    for page in row.get('pages',[]):
        soup=BeautifulSoup(Path(page['file']).read_text(encoding='utf-8'),'html.parser')
        for node in soup.select('script,style,noscript,form'):node.decompose()
        text=re.sub(r'\s+',' ',soup.get_text(' ',strip=True));titles.append(page['title'])
        for match in phonenumbers.PhoneNumberMatcher(text,'KZ'):
            numbers.add(phonenumbers.format_number(match.number,phonenumbers.PhoneNumberFormat.E164))
        for email in page['emails']:
            index=text.lower().find(email.lower())
            if index>=0:contexts[email]=text[max(0,index-90):index+len(email)+90]
    row['matching_directory_phones']=sorted(numbers & {p['number'] for p in seeds[row['source_id']]['phones']})
    row['review_contexts']=contexts
    row['identity_review']='Другая тематика сайта, не дополнять клиента' if row['source_id']=='9197' else 'Сверка юридического лица не выполнена'
    if row['source_id']=='9197':row['emails']=[]
ROOT.joinpath('reviewed-results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
emails=sorted({e['email'] for r in rows for e in r.get('emails',[])})
summary={'websites_attempted':len(rows),'websites_with_pages':sum(bool(r.get('pages')) for r in rows),'websites_failed_or_restricted':sum(not bool(r.get('pages')) for r in rows),'websites_with_emails':sum(bool(r.get('emails')) for r in rows),'unique_emails':len(emails),'emails':emails,'different_site_subject':1,'with_matching_directory_phone':sum(bool(r['matching_directory_phones']) for r in rows)}
ROOT.joinpath('summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=True,indent=2))
for row in rows:
    if row.get('review_contexts'):print(row['source_id'],json.dumps(row['review_contexts'],ensure_ascii=True))

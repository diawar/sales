"""Append verified URLs and independently check the first ten new cards."""
import os,sys,json,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','mailer.settings')
import django
django.setup()
from contacts.models import GovCollection
from contacts.gov_collection import control,save_card
from contacts.gov_source import canonical_url,read_card
from contacts.collection import log

snapshot=json.loads(Path('data/gov-inspection/maslikhats-discovered.json').read_text(encoding='utf-8'))
if snapshot['source']!='https://www.gov.kz/?lang=ru':raise ValueError('Unexpected source')
urls=list(dict.fromkeys(canonical_url(e['url']) for e in snapshot['entries']))
if len(urls)!=227:raise ValueError('Unexpected snapshot size')
state=GovCollection.objects.get(pk=1)
if state.enabled:raise SystemExit('Pause collector before pilot.')
new=[url for url in urls if url not in state.urls]
state=control('append',urls)
report={'total_urls':len(state.urls),'queue_cursor':state.cursor,'requested':0,'saved':0,'created':0,'updated':0,'emails':[],'phones':[],'errors':[]}
try:
    for url in (new or urls)[:10]:
        report['requested']+=1
        try:
            row=read_card(url);client,created,conflicts=save_card(row)
            report['saved']+=1;report['created']+=int(created);report['updated']+=int(not created)
            report['emails']=sorted(set(report['emails'])|set(row['emails']))
            report['phones']=sorted(set(report['phones'])|{p['number'] for p in row['phones']})
            log(f'Gov.kz: пилот маслихатов, карточка #{client.pk} сохранена.',source='gov')
        except Exception as exc:
            report['errors'].append({'url':url,'reason':str(exc)[:300]})
            log('Gov.kz: пилот маслихатов остановлен после ошибки; очередь сохранена.','warning',source='gov')
            break
        time.sleep(3)
finally:
    Path('data/gov-inspection/maslikhats-pilot.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({**report,'emails':len(report['emails']),'phones':len(report['phones'])},ensure_ascii=True))

"""Merge verified public directory links into the existing collector list."""
import os,sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','mailer.settings')
import django
django.setup()
from contacts.models import GovCollection
from contacts.gov_collection import control
from contacts.gov_source import canonical_url

snapshot=json.loads(Path('data/gov-inspection/catalog-discovered.json').read_text(encoding='utf-8'))
if snapshot['source']!='https://www.gov.kz/?lang=ru':raise ValueError('Unexpected directory source')
found=list(dict.fromkeys(canonical_url(row['url']) for row in snapshot['entries']))
if len(found)!=58:raise ValueError('Unexpected directory size; review snapshot first')
state,_=GovCollection.objects.get_or_create(pk=1)
if state.enabled:raise SystemExit('Pause before configuring; no state changed.')
new=[url for url in found if url not in state.urls]
combined=list(dict.fromkeys(new+state.urls))
control('configure',combined)
state=control('start')
print(json.dumps({'total':len(state.urls),'new_urls':len(new),'enabled':state.enabled}))

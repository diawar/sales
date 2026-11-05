"""Import at most two checked candidates from each purchase segment."""
import os
import sys
import json
import hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','mailer.settings')
import django
django.setup()
from contacts.overshop_import import select_pilot, import_pilot
from contacts.models import Client, Contact

path=Path('data/overshop/full-review.json')
raw=path.read_bytes();report=json.loads(raw)
selected=select_pilot(report['records'])
result=import_pilot(selected,hashlib.sha256(raw).hexdigest())
for row in selected:
    client=Client.objects.get(identity_key=row['identity_key'])
    assert Contact.objects.filter(client=client,address__email=row['email'],audiences__id=result['audience_id']).exists()
    assert client.facts.filter(field='purchase_snapshot',source='Over-shop.kz').exists()
result['verified']=True
Path('data/overshop/pilot-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=True))

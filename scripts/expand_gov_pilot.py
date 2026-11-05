"""Bounded expansion using public organization cards; pause for acceptance."""
import os,sys,json,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','mailer.settings')
import django
django.setup()
from contacts.models import GovCollection
from contacts.gov_collection import control,run_round

state,_=GovCollection.objects.get_or_create(pk=1)
if state.enabled:raise SystemExit('Collector enabled; pause before changing its list.')
urls=list(dict.fromkeys(state.urls+[f'https://www.gov.kz/org/{slug}?lang=ru' for slug in ['almaty','mam','mps','minfin','moa','dsm','energo','economy']]))
if '--resume' not in sys.argv:control('configure',urls)
control('start')
try:
    for _ in range(180):
        run_round();state.refresh_from_db()
        if state.completed_at or state.error:break
        time.sleep(1)
finally:control('pause')
state.refresh_from_db()
print(json.dumps({'cursor':state.cursor,'total':len(state.urls),'report':state.report,'error':state.error,'enabled':state.enabled},ensure_ascii=True))

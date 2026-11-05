"""Import the freshly inspected public AFM card without rewinding the queue."""
import os,sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','mailer.settings')
import django
django.setup()
from contacts.gov_source import parse_card
from contacts.gov_collection import save_card
from contacts.collection import log
row=parse_card(Path('data/gov-inspection/afm-contacts.html').read_text(encoding='utf-8'),'https://www.gov.kz/org/afm?lang=ru')
client,new,conflicts=save_card(row)
log(f'Gov.kz: восстановлена карточка afm, клиент #{client.pk}; курсор основного обхода сохранён.',source='gov')
print(json.dumps({'created':new,'emails':len(row['emails']),'phones':len({p['number'] for p in row['phones']}),'conflicts':len(conflicts)}))

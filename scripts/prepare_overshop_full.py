"""Prepare complete ID-based review lists without importing customers."""
import os,sys,json,hashlib
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','mailer.settings')
from contacts.overshop_source import parse_buyers, prepare_users

path=Path(sys.argv[1] if len(sys.argv)>1 else 'C:/Users/Dmitriy Small/Downloads/user_admin.xls')
orders_path=Path(sys.argv[2] if len(sys.argv)>2 else 'C:/Users/Dmitriy Small/Downloads/result.csv')
buyers_path=Path(sys.argv[3] if len(sys.argv)>3 else 'C:/Users/Dmitriy Small/Downloads/sale_buyers (1).xls')
raw=path.read_bytes();orders_raw=orders_path.read_bytes();buyers_raw=buyers_path.read_bytes()
records=prepare_users(raw.decode('utf-8-sig'),orders_raw.decode('utf-8-sig'),parse_buyers(buyers_raw.decode('utf-8-sig')))
counts=Counter(r['category'] for r in records)
summary={'records':len(records),'unique_ids':len({r['source_customer_id'] for r in records}),'categories':dict(counts),
    'purchases':dict(Counter(r['purchase_segment'] for r in records)),
    'candidate_purchases':dict(Counter(r['purchase_segment'] for r in records if r['category']=='person_candidate')),
    'with_issues':sum(bool(r['issues']) for r in records),'city_present':sum(bool(r['city']) for r in records),
    'source_sha256':hashlib.sha256(raw).hexdigest(),'orders_sha256':hashlib.sha256(orders_raw).hexdigest(),
    'buyers_sha256':hashlib.sha256(buyers_raw).hexdigest(),'imported':False,'physical_type_confirmed':False}
folder=Path('data/overshop');folder.mkdir(exist_ok=True)
(folder/'full-review.json').write_text(json.dumps({'summary':summary,'records':records},ensure_ascii=False,indent=2),encoding='utf-8')
for category in ['person_candidate','staff','organization_review','service_review']:
    (folder/(category+'.json')).write_text(json.dumps([r for r in records if r['category']==category],ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=True))

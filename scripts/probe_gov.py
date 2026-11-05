"""Parse already downloaded public sample, without database writes."""
import os,sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','mailer.settings')
from contacts.gov_source import parse_card
folder=Path('data/gov-inspection')
report=[]
for slug in ('kazyna','edu','astana'):
    try:row=parse_card(folder.joinpath(slug+'-contacts.html').read_text(encoding='utf-8'),f'https://www.gov.kz/org/{slug}/contacts?lang=ru')
    except ValueError as exc:
        report.append({'source':slug,'error':str(exc)});continue
    report.append({'source':slug,'name':row['name'],'emails':len(row['emails']),'phones':len({p['number'] for p in row['phones']}),'purposes':sorted({p['purpose'] for p in row['phones']}),'rows':len(row['source_rows']),'issues':row['issues']})
folder.joinpath('report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True,indent=2))

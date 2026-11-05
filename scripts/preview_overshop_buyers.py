"""Read-only analysis of Bitrix's HTML-as-XLS export. No database writes."""
from html.parser import HTMLParser
from pathlib import Path
from collections import Counter
import json,os,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','mailer.settings')
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from contacts.phones import parse_phone

class ExportTable(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.rows=[];self.row=None;self.cell=None
    def handle_starttag(self,tag,attrs):
        if tag=='tr':self.row=[]
        elif tag in {'td','th'} and self.row is not None:self.cell=[]
        elif tag=='br' and self.cell is not None:self.cell.append('\n')
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in {'td','th'} and self.cell is not None:
            self.row.append(''.join(self.cell).strip());self.cell=None
        elif tag=='tr' and self.row is not None:
            self.rows.append(self.row);self.row=None

path=Path(sys.argv[1] if len(sys.argv)>1 else 'C:/Users/Dmitriy Small/Downloads/sale_buyers.xls')
if path.stat().st_size>10_000_000:raise ValueError('File too large')
parser=ExportTable();parser.feed(path.read_text(encoding='utf-8-sig'))
headers=parser.rows[0]
required={'Покупатель','E-mail','Телефон','Группы покупателя'}
if not required.issubset(headers):raise ValueError('Unexpected export columns')
rows=[dict(zip(headers,row)) for row in parser.rows[1:] if len(row)==len(headers)]
stats=Counter();groups=Counter();preview=[];emails=set()
for index,row in enumerate(rows,2):
    group=[g.strip() for g in row['Группы покупателя'].splitlines() if g.strip()]
    groups.update(group)
    staff=any('администратор' in g.casefold() for g in group)
    stats['administrators' if staff else 'requires_customer_type_review']+=1
    email=row['E-mail'].strip().lower();valid=False
    if email:
        try:validate_email(email);valid=True;emails.add(email);stats['valid_email']+=1
        except ValidationError:stats['invalid_email']+=1
    else:stats['without_email']+=1
    phone=row['Телефон'].strip();kind=parse_phone(phone)['kind'] if phone else None
    if kind:stats['phone_'+kind]+=1
    else:stats['without_phone']+=1
    if not staff and len(preview)<10:
        preview.append({'source_row':index,'name':row['Покупатель'],'email':email,'phone':phone,'phone_kind':kind,
            'city':None,'source_customer_id':None,'customer_type':'Не подтверждён','marketing_consent':'Неизвестно','groups':group})
report={'source':str(path),'format':'Bitrix HTML table exported as .xls','headers':headers,'records':len(rows),
    'unique_valid_emails':len(emails),'stats':dict(stats),'groups':dict(groups),'can_import':False,
    'blockers':['В выгрузке нет ID клиента','Тип физлицо/организация не подтверждён','Город отсутствует в выбранных колонках'],
    'preview':preview}
sample_path=Path('data/overshop/buyers-sample-dom.json')
if sample_path.exists():
    checked=[]
    for item in json.loads(sample_path.read_text(encoding='utf-8')):
        cells=item['cells']
        if not str(item['id']).isdigit() or len(cells)!=12:raise ValueError('Unexpected DOM sample')
        email=cells[3].strip().lower()
        matching=[r for r in rows if r['E-mail'].strip().lower()==email and r['Покупатель']==cells[2] and r['Дата регистрации']==cells[5]]
        if len(matching)!=1:raise ValueError('Sample no longer matches export; obtain a fresh sample')
        checked.append({'source_customer_id':item['id'],'name':cells[2],'email':email,'phone':cells[4],
            'phone_kind':parse_phone(cells[4])['kind'] if cells[4] else None,'city':None,
            'customer_type':'Не подтверждён','marketing_consent':'Неизвестно','registered_at':cells[5]})
    report['preview']=checked
    report['sample_has_ids']=True
folder=Path('data/overshop');folder.mkdir(exist_ok=True)
(folder/'buyers-preview.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k not in {'preview','source'}},ensure_ascii=True))

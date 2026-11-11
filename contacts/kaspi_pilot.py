import hashlib
import json
import uuid
from urllib.parse import urlsplit
from django.db import transaction
from django.utils import timezone
from .models import Client,Audience,ImportBatch,CollectionLog
from .profiles import apply_profile,attach_contacts,fact
from .kaspi_sample import parse_seller_html

ALLOWED_CATEGORIES={'Компьютеры','Комплектующие','Процессоры','Системные блоки'}

def parse_samples(samples):
    if not isinstance(samples,list) or not 1<=len(samples)<=10:raise ValueError('Пилот: от 1 до 10 карточек.')
    rows={}
    for sample in samples:
        if len(sample['html'])>1000000:raise ValueError('Слишком большая карточка.')
        row=parse_seller_html(sample['html'],sample['source_url'])
        if not row['merchant_id'] or not row['shop_name']:raise ValueError('В карточке нет стабильного ID или имени.')
        if not row['phones'] and not row['emails']:raise ValueError('Нет опубликованных контактов продавца.')
        categories=sample['categories']
        if not isinstance(categories,list) or not categories or any(c not in ALLOWED_CATEGORIES for c in categories):
            raise ValueError('Категория вне согласованного пилота.')
        discovery=sample['discovery_url'];parts=urlsplit(discovery)
        if parts.scheme!='https' or parts.netloc!='kaspi.kz' or not parts.path.startswith('/shop/p/'):
            raise ValueError('Нужна ссылка на товар-источник.')
        row.update(categories=list(dict.fromkeys(categories)),discovery_urls=[discovery])
        previous=rows.get(row['merchant_id'])
        if previous:
            if previous['shop_name']!=row['shop_name']:raise ValueError('Разные названия одного ID — требуется проверка.')
            for key in ['categories','discovery_urls','phones','emails','addresses','address_cities']:
                previous[key]+=[v for v in row[key] if v not in previous[key]]
            previous['city']=previous['address_cities'][0] if len(previous['address_cities'])==1 else ''
        else:rows[row['merchant_id']]=row
    return list(rows.values())

def audience(name,parent):
    return Audience.objects.get_or_create(name_key=f'{parent.pk if parent else "root"}:'+name.casefold(),
        defaults={'name':name,'parent':parent})[0]

@transaction.atomic
def import_samples(samples):
    rows=parse_samples(samples)
    digest=hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    token=uuid.uuid5(uuid.NAMESPACE_URL,'kaspi:pilot:'+digest)
    previous=ImportBatch.objects.filter(token=token).first()
    if previous:return {**previous.result,'repeated':True}
    root=audience('Бизнес',None);base=audience('Kaspi',root)
    result={'kind':'kaspi_pilot','created':0,'updated':0,'merchants':len(rows),'repeated':False,
            'phones':len({p['number'] for row in rows for p in row['phones']}),
            'emails':len({e for row in rows for e in row['emails']}),'completed_at':timezone.now().isoformat()}
    for row in rows:
        client,new=Client.objects.get_or_create(identity_key='kaspi:merchant:'+row['merchant_id'],defaults={'name':row['shop_name']})
        apply_profile(client,{'name':row['shop_name'],'city':row['city']},'Kaspi',row['source_url'])
        audiences=[root,base]+[audience(c,base) for c in row['categories']]
        attach_contacts(client,row['emails'],[p['raw'] for p in row['phones']],audiences,'Kaspi',row['source_url'])
        for key,value in {'kaspi_merchant_id':row['merchant_id'],'addresses':row['addresses'],
                          'kaspi_categories':row['categories'],'kaspi_discovery':row['discovery_urls'],
                          'kaspi_view_city':row['view_city']}.items():
            fact(client,key,value,'Kaspi',row['source_url'],'accepted')
        result['created' if new else 'updated']+=1
    ImportBatch.objects.create(token=token,audience=base,rows=rows,result=result,committed_at=timezone.now())
    CollectionLog.objects.create(source='kaspi',message=f'Пилот Kaspi: обработано магазинов {len(rows)}, создано {result["created"]}, обновлено {result["updated"]}.')
    return result

"""Small, repeatable import of reviewed Over-shop candidates only."""
import uuid
from django.db import transaction
from django.core.validators import validate_email
from .models import Audience, Client, ImportBatch
from .profiles import apply_profile, attach_contacts, fact
from .overshop_source import PURCHASE_LABELS


def select_pilot(records):
    selected=[]
    for segment in PURCHASE_LABELS:
        candidates=sorted((r for r in records if r['category']=='person_candidate'
                           and not r['issues'] and r['purchase_segment']==segment),
                          key=lambda r:int(r['source_customer_id']),reverse=True)
        selected.extend(candidates[:2])
    return selected


@transaction.atomic
def import_pilot(records, source_hash):
    if not records or len(records)>10:
        raise ValueError('Пилот ограничен 1–10 клиентами.')
    ids=[]
    for row in records:
        identifier=row['source_customer_id']
        if (not identifier.isascii() or not identifier.isdigit() or int(identifier)<1 or
                row['identity_key']!='overshop:user:'+identifier or identifier in ids or
                row['category']!='person_candidate' or row['issues'] or
                row['purchase_segment'] not in PURCHASE_LABELS or not row['name']):
            raise ValueError('В пилоте допустимы только кандидаты без замечаний и с уникальными ID.')
        validate_email(row['email'])
        ids.append(identifier)
    token=uuid.uuid5(uuid.NAMESPACE_URL,'overshop:pilot:'+source_hash+':'+','.join(sorted(ids)))
    previous=ImportBatch.objects.filter(token=token,committed_at__isnull=False).first()
    if previous:
        return {**previous.result,'repeated':True}
    root,_=Audience.objects.get_or_create(name_key='root:физические лица',defaults={'name':'Физические лица'})
    audience,_=Audience.objects.get_or_create(name_key=f'{root.id}:over-shop.kz',defaults={'name':'Over-shop.kz','parent':root})
    result={'created':0,'updated':0,'source_ids':ids,'client_ids':[],'audience_id':audience.id,'repeated':False}
    for row in records:
        identifier=row['source_customer_id']
        source_url='https://over-shop.kz/bitrix/admin/user_edit.php?lang=ru&ID='+identifier
        client,created=Client.objects.get_or_create(identity_key=row['identity_key'],defaults={'name':row['name']})
        apply_profile(client,{'name':row['name'],'city':row['city'] or ''},'Over-shop.kz',source_url)
        segment_name=PURCHASE_LABELS[row['purchase_segment']]
        segment,_=Audience.objects.get_or_create(name_key=f'{audience.id}:'+segment_name.casefold(),defaults={'name':segment_name,'parent':audience})
        attach_contacts(client,[row['email']],row['phones'],[root,audience,segment],'Over-shop.kz',source_url)
        fact(client,'source_customer_id',identifier,'Over-shop.kz',source_url,'accepted')
        fact(client,'purchase_snapshot',{k:row[k] for k in ['purchase_segment','orders_total','orders_paid','orders_cancelled']},'Over-shop.kz',source_url,'accepted')
        result['created' if created else 'updated']+=1
        result['client_ids'].append(client.id)
    from django.utils import timezone
    ImportBatch.objects.create(token=token,audience=audience,rows=[{'source_customer_id':i} for i in ids],
                               committed_at=timezone.now(),result=result)
    return result

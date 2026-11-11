"""Import authorised snapshots; account IDs and subscription IDs are separate identities."""
import uuid
from collections import Counter
from django.db import transaction
from django.utils import timezone
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from .models import Audience, Client, Address, Contact, ImportBatch, OverShopSubscription
from .profiles import apply_profile, attach_contacts, fact
from .overshop_source import PURCHASE_LABELS


def audience(name,parent=None):
    return Audience.objects.get_or_create(name_key=f'{parent.pk if parent else "root"}:'+name.casefold(),
        defaults={'name':name,'parent':parent})[0]


def subscription_state(subscription):
    if subscription.review_reason:return 'review'
    if subscription.address.archived or subscription.address.suppression_reason:
        return 'suppressed'
    if not subscription.active or not subscription.rubrics:
        return 'inactive'
    return 'confirmed' if subscription.confirmed else 'pending'


@transaction.atomic
def import_snapshot(customers,subscribers,source_hash):
    token=uuid.uuid5(uuid.NAMESPACE_URL,'overshop:full:'+source_hash)
    previous=ImportBatch.objects.filter(token=token,committed_at__isnull=False).first()
    if previous:return {**previous.result,'repeated':True}
    ids=[r['source_customer_id'] for r in customers]
    if len(set(ids))!=len(ids):raise ValueError('Повторный ID клиента.')
    sub_ids=[r['id'] for r in subscribers]
    if len(set(sub_ids))!=len(sub_ids):raise ValueError('Повторный ID подписки.')
    root=audience('Физические лица');base=audience('Over-shop.kz',root)
    purchase_audiences={k:audience(v,base) for k,v in PURCHASE_LABELS.items()}
    subscribers_audience=audience('Подписчики сайта',base)
    state_audiences={k:audience(v,subscribers_audience) for k,v in {
        'confirmed':'Подтверждена','pending':'Ожидает подтверждения',
        'inactive':'Неактивна','suppressed':'Исключены из отправки','review':'На проверку'}.items()}
    report={'kind':'overshop_full','accounts_seen':len(customers),'created':0,'updated':0,
            'excluded':dict(Counter(r['category'] for r in customers if r['category']!='person_candidate')),
            'accounts_with_issues':0,'subscriptions':len(subscribers),'anonymous_created':0,
            'subscription_states':{},'excluded_subscriber_accounts':0,'repeated':False,
            'started_at':timezone.now().isoformat(),'source_hash':source_hash}
    imported={}
    excluded_emails={r['email'] for r in customers if r['category']!='person_candidate' and r['email']}
    for row in customers:
        if row['category']!='person_candidate':continue
        identifier=row['source_customer_id']
        if row['identity_key']!='overshop:user:'+identifier:raise ValueError('Несовпадение ID клиента.')
        url='https://over-shop.kz/bitrix/admin/user_edit.php?lang=ru&ID='+identifier
        name=row['name'] or 'Клиент Over-shop №'+identifier
        client,created=Client.objects.get_or_create(identity_key=row['identity_key'],defaults={'name':name})
        apply_profile(client,{'name':name,'city':row['city'] or ''},'Over-shop.kz',url)
        try:validate_email(row['email']);emails=[row['email']]
        except ValidationError:emails=[]
        attach_contacts(client,emails,row['phones'],[root,base],'Over-shop.kz',url)
        for contact in client.contact_set.all():
            contact.audiences.remove(*purchase_audiences.values())
            contact.audiences.add(purchase_audiences[row['purchase_segment']])
        for key,value in {'source_customer_id':identifier,'registered_at':row['registered_at'],
                          'purchase_snapshot':{k:row[k] for k in ['purchase_segment','orders_total','orders_paid','orders_cancelled']},
                          'source_issues':row['issues']}.items():
            client.facts.filter(field=key,source='Over-shop.kz',state='accepted').update(state='superseded')
            fact(client,key,value,'Over-shop.kz',url,'accepted')
        if not emails:fact(client,'invalid_source_email',row['email'],'Over-shop.kz',url,'alternative')
        imported[identifier]=client
        report['created' if created else 'updated']+=1
        report['accounts_with_issues']+=bool(row['issues'])
    states=Counter();imported_client_ids={c.pk for c in imported.values()}
    for row in subscribers:
        address,_=Address.objects.get_or_create(email=row['email'])
        customer_id=row['customer_id'];client=imported.get(customer_id)
        excluded=bool((customer_id and customer_id not in imported) or
                      (not customer_id and row['email'] in excluded_emails))
        url='https://over-shop.kz/bitrix/admin/subscr_edit.php?lang=ru&ID='+row['id']
        # A subscription without USER_ID is address-level evidence, not proof of account ownership.
        contacts=list(Contact.objects.filter(address=address,client__identity_key__startswith='overshop:user:')) if not customer_id else []
        contacts=[c for c in contacts if c.client_id in imported_client_ids]
        if excluded:
            contacts=[];report['excluded_subscriber_accounts']+=1
        elif client:
            contacts=attach_contacts(client,[row['email']],[],[root,base],'Over-shop.kz — подписка',url)
        elif not contacts:
            client,created=Client.objects.get_or_create(identity_key='overshop:subscriber:'+row['id'],
                defaults={'name':'Подписчик Over-shop №'+row['id']})
            contacts=attach_contacts(client,[row['email']],[],[root,base],'Over-shop.kz — подписка',url)
            report['anonymous_created']+=created
        record,_=OverShopSubscription.objects.update_or_create(subscriber_id=row['id'],defaults={
            'source_customer_id':customer_id,'address':address,'client':client,'active':row['active'],
            'confirmed':row['confirmed'],'rubrics':row['rubrics'],'source_created_at':row['created_at'],
            'source_updated_at':row['updated_at'],'source_confirmed_at':row['confirmed_at'],'source_hash':source_hash,
            'review_reason':'Аккаунт или email связан с исключённой записью' if excluded else ''})
        state=subscription_state(record);states[state]+=1
        for contact in contacts:
            contact.audiences.add(subscribers_audience)
            contact.audiences.remove(*state_audiences.values())
            contact.audiences.add(state_audiences[state])
            fact(contact.client,'subscription_snapshot',row,'Over-shop.kz — подписка',url,'accepted')
    report['subscription_states']=dict(states)
    report['completed_at']=timezone.now().isoformat()
    ImportBatch.objects.create(token=token,audience=base,rows=[],result=report,committed_at=timezone.now())
    return report

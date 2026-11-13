"""Circuit breaker for synthetic campaign outcomes, isolated to one rehearsal."""
from decimal import Decimal, InvalidOperation
from django.db.models import Count, Q
from django.utils import timezone
from .models import RehearsalLetter
from .rehearsal_delivery import OUTCOMES, PENDING
from . import action_audit as audit

DEFAULT = {'enabled':True,'min_sample':20,'errors_percent':'10','permanent_percent':'5','complaints_percent':'0.1'}
DISABLED = {**DEFAULT,'enabled':False}
LABELS = {'errors':'Письма с любыми ошибками','permanent':'Письма с постоянной ошибкой','complaints':'Письма с жалобой'}


def policy(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULT):raise ValueError('Проверьте настройки автоматической остановки.')
    result={**DEFAULT,**value}
    if type(result['enabled']) is not bool:raise ValueError('Включение защиты: true или false.')
    if type(result['min_sample']) is not int or not 1<=result['min_sample']<=10000:
        raise ValueError('Минимум писем с результатом: целое число от 1 до 10 000.')
    for name in ('errors_percent','permanent_percent','complaints_percent'):
        raw=result[name]
        if type(raw) not in (str,int,float) or len(str(raw))>16:raise ValueError('Порог: число от 0 до 100%, не более двух знаков после точки.')
        try:
            d=Decimal(str(raw))
            if not d.is_finite() or not 0<=d<=100 or d!=d.quantize(Decimal('.01')):raise InvalidOperation
        except InvalidOperation:raise ValueError('Порог: число от 0 до 100%, не более двух знаков после точки.') from None
        result[name]=format(d,'f')
    return result


def assessment(row):
    config=row.snapshot.get('safety_policy',DISABLED)
    condition=Q(delivery_events__kind__in=OUTCOMES,delivery_events__occurred_at__lte=row.clock_at)
    letters=RehearsalLetter.objects.filter(rehearsal=row,message__is_test=True).filter(condition)
    groups=letters.values('message__campaign_id').annotate(sample=Count('pk',distinct=True),
        errors=Count('pk',filter=Q(delivery_events__kind__in=('temporary_error','permanent_error')),distinct=True),
        permanent=Count('pk',filter=Q(delivery_events__kind='permanent_error'),distinct=True),
        complaints=Count('pk',filter=Q(delivery_events__kind='complaint'),distinct=True)).order_by('message__campaign_id')
    names={s['campaign_id']:s['campaign_name'] for s in row.snapshot['steps']}
    campaigns=[];breaches=[]
    for group in groups:
        cid=group['message__campaign_id'];sample=group['sample'];metrics={}
        for key in LABELS:
            threshold=config[{'errors':'errors_percent','permanent':'permanent_percent','complaints':'complaints_percent'}[key]]
            exceeded=bool(config['enabled'] and sample>=config['min_sample'] and group[key]*100>Decimal(threshold)*sample)
            metrics[key]={'count':group[key],'percent':format((Decimal(group[key])*100/sample).quantize(Decimal('.0001')),'f'),
                'threshold':threshold,'exceeded':exceeded}
            if exceeded:breaches.append({'campaign_id':cid,'campaign_name':names.get(cid,'Рассылка'),
                'metric':key,'title':LABELS[key],'sample':sample,**metrics[key]})
        campaigns.append({'campaign_id':cid,'name':names.get(cid,'Рассылка'),'sample':sample,
            'enough_data':sample>=config['min_sample'],'metrics':metrics})
    return {'policy':config,'revision':row.snapshot.get('safety_revision',0),'campaigns':campaigns,
        'breaches':breaches,'stop':row.snapshot.get('safety_stop'),'can_send':False}


def evaluate(row,log):
    # Called under the same rehearsal lock as outcomes and processing.
    if row.snapshot.get('safety_stop'):
        if row.state not in ('safety_stopped','cancelled'):
            row.state='safety_stopped';row.save(update_fields=['state'])
        return False
    if row.state=='cancelled':return False
    data=assessment(row)
    if not data['breaches']:return False
    reasons=[]
    for b in data['breaches']:
        reasons.append(f'Рассылка #{b["campaign_id"]} «{b["campaign_name"]}»: {b["title"]} — {b["count"]}/{b["sample"]} ({b["percent"]}%), порог > {b["threshold"]}%.')
    detail='Автоматическая остановка. '+' '.join(reasons)
    row.snapshot['safety_stop']={'clock_at':row.clock_at.isoformat(),'recorded_at':timezone.now().isoformat(),
        'policy_revision':data['revision'],'policy':data['policy'],'campaigns':data['campaigns'],
        'breaches':data['breaches'],'reason':detail}
    previous=row.state
    row.state='safety_stopped'
    for member in row.members:
        if member['state']=='waiting':member.update(state='safety_stopped',detail='Рассылка остановлена защитой. '+detail)
    row.save(update_fields=['snapshot','state','members'])
    pending=row.letters.filter(delivery_state__in=PENDING)
    count=pending.count()
    pending.update(delivery_state='stopped',retry_at=None,delivery_detail='Повтор остановлен защитой рассылки.')
    log(row,'safety_stopped',detail+f' Ожидающих повторов остановлено: {count}. Новые письма не подготавливаются.',audit_before={'state':previous})
    return True


def configure(row,data,log):
    if row.state in ('cancelled','safety_stopped') or row.snapshot.get('safety_stop'):
        raise ValueError('После остановки защитой или отмены создайте новую репетицию. Причина остановки сохраняется.')
    revision=row.snapshot.get('safety_revision',0)
    if type(data.get('policy_revision')) is not int or data['policy_revision']!=revision:
        raise ValueError('Пороги изменились. Обновите репетицию перед сохранением.')
    config=policy(data.get('policy'))
    before={'policy':row.snapshot.get('safety_policy',DISABLED),'revision':revision}
    row.snapshot['safety_policy']=config;row.snapshot['safety_revision']=revision+1
    row.save(update_fields=['snapshot'])
    audit.record('settings_changed','rehearsal',row.pk,row.snapshot['name'],campaigns=audit.scenario_campaigns(row.snapshot),
        before=before,after={'policy':config,'revision':revision+1},summary='Пороги автоматической остановки изменены.')
    log(row,'safety_configured',f'Защита {"включена" if config["enabled"] else "выключена"}; минимум результатов: {config["min_sample"]}; любые ошибки > {config["errors_percent"]}%, постоянные > {config["permanent_percent"]}%, жалобы > {config["complaints_percent"]}%. Версия {revision+1}.')
    evaluate(row,log)

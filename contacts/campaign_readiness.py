"""Read-only readiness report. No DNS, URL requests, SMTP or event ingestion."""
import re
from collections import Counter,defaultdict
from urllib.parse import urlsplit,unquote
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from django.core.validators import validate_email,URLValidator
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import Address,Contact,Audience,OverShopSubscription,EmailExclusion,EmailSubscription
from .campaign_views import audience_selection,recipients
from .email_policy import sending_state
from .template_tools import check_template,personalize,TOKEN


def url_issue(value,attribute):
    try:
        parsed=urlsplit(value.strip());scheme=parsed.scheme.lower()
        if scheme in ('http','https'):
            URLValidator(schemes=['http','https'])(value)
            if parsed.username or parsed.password:return 'Ссылка содержит логин или пароль.'
            return ''
        if attribute=='href' and scheme=='mailto':
            validate_email(unquote(parsed.path));return ''
        if attribute=='href' and scheme=='tel' and re.fullmatch(r'\+?[0-9(). -]{3,40}',unquote(parsed.path)):return ''
    except (ValueError,ValidationError):pass
    return 'Некорректная или небезопасная ссылка после подстановки.'


def report(campaign):
    now=timezone.now();checks=[]
    def add(section,code,status,title,detail):
        checks.append({'section':section,'code':code,'status':status,'title':title,'detail':detail})
    from .campaign_approval import require_approved
    try:
        require_approved(campaign,recipients=not hasattr(campaign,'dispatch_queue'))
        add('Согласование','approval','pass','Одобрение версии','Актуальная версия одобрена для имитации.')
    except ValueError as exc:add('Согласование','approval','fail','Одобрение версии',str(exc))
    queue=getattr(campaign,'dispatch_queue',None)
    template=queue.snapshot.get('template',{}) if queue else campaign.template_snapshot
    sender=queue.snapshot.get('sender',{}) if queue else campaign.sender_snapshot
    audience_ids=list(campaign.audiences.values_list('pk',flat=True))
    source_counts=recipients({'audience_ids':audience_ids}) if audience_ids else {key:0 for key in ('contacts','clients','without_email','unique_emails','duplicates','archived','suppressed','invalid','pending','ready')}
    expanded=audience_selection({'audience_ids':audience_ids})[1] if audience_ids else set()
    source_contacts=Contact.objects.filter(audiences__pk__in=expanded).distinct()
    selected_ids=list(source_contacts.exclude(address=None).values_list('address_id',flat=True))
    frozen=list(queue.items.values('address_id','email','state')) if queue else None
    keys={r['email'] for r in frozen} if queue else {email.strip().casefold() for email in Address.objects.filter(pk__in=selected_ids).values_list('email',flat=True)}
    addresses=defaultdict(list)
    for address in Address.objects.only('email','archived','suppression_reason','validation_result','validation','validation_checked_at').iterator(chunk_size=1000):
        key=address.email.strip().casefold()
        if key in keys:addresses[key].append(address)
    from .subscriptions import revoked_emails
    central_revoked=set(revoked_emails())
    technical={email for email,rows in addresses.items() if email not in central_revoked and all(sending_state(a)['code']=='ready' for a in rows)}
    active_keys={r['email'] for r in frozen if r['state'] in ('pending','retry')} if queue else keys
    if queue:
        by_id={a.pk:a for rows in addresses.values() for a in rows}
        technical={item['email'] for item in frozen if item['email'] in technical and item['address_id'] in by_id and item['email'] in active_keys}
    add('Получатели','recipients','pass' if technical else 'fail','Актуальный допуск получателей',f'После технических исключений: {len(technical)} из {len(active_keys)} уникальных email, ожидающих обработки. Проверка не подтверждает существование ящика.')
    if source_counts['pending']:
        add('Получатели','pending_dns','warning','Есть адреса без достоверной проверки',f'В текущих аудиториях: {source_counts["pending"]}. Они не допущены к отправке.')
    dated=[a for key in technical for a in addresses[key]]
    old=sum(a.validation_checked_at is None or (now-a.validation_checked_at).days>30 for a in dated)
    if old:add('Получатели','old_dns','warning','Давность проверки email',f'У {old} записей нет даты проверки или она старше 30 дней. DNS в этой проверке повторно не запрашивается.')
    if queue:
        unavailable=len(active_keys)-len(technical)
        add('Очередь','queue_exclusions','warning' if unavailable else 'pass','Перепроверка зафиксированного состава',f'В очереди {len(keys)} адресов; ожидают обработки {len(active_keys)}, из них сейчас технически допущено {len(technical)}. Новые контакты в эту очередь не добавляются.')
        add('Очередь','queue_state','fail' if queue.state in ('completed','cancelled') else 'pass','Состояние очереди',{'paused':'На паузе.','running':'Идёт имитация.','completed':'Имитация завершена; для нового запуска нужна новая рассылка.','cancelled':'Очередь отменена; для нового запуска нужна новая рассылка.'}.get(queue.state,queue.state))
    else:add('Очередь','queue_missing','warning','Очередь ещё не подготовлена','Состав будет зафиксирован при подготовке очереди. Сейчас рассчитаны выбранные аудитории и подкатегории.')
    # Only imported subscription evidence is recognised. Registration/publication is never consent.
    ids=[a.pk for rows in addresses.values() for a in rows]
    revoked_ids=set(EmailExclusion.objects.filter(address_id__in=ids,reason='unsubscribe',removed_at=None).values_list('address_id',flat=True))
    subscriptions=defaultdict(list)
    for row in OverShopSubscription.objects.filter(address_id__in=ids):subscriptions[row.address_id].append(row)
    central={r.email:r for r in EmailSubscription.objects.filter(email__in=keys,is_test=False,scope='over_email_offers')}
    consent={};evidence_counts=Counter()
    for email in keys:
        rows=addresses.get(email,[])
        revoked=email in central_revoked or any(a.pk in revoked_ids or 'отпис' in a.suppression_reason.casefold() for a in rows)
        local=central.get(email)
        evidence=bool(local and local.state=='confirmed' and local.confirmed_at and local.consent_text and local.consent_version) or any(s.active and s.confirmed and s.rubrics and not s.review_reason and s.source_hash.strip() and s.source_confirmed_at.strip() for a in rows for s in subscriptions[a.pk])
        state='revoked' if revoked else 'confirmed' if evidence else 'unknown'
        consent[email]=state;evidence_counts[state]+=1
    eligible_consent=Counter(consent[email] for email in technical)
    add('Согласия','consent','fail' if eligible_consent['unknown'] or eligible_consent['revoked'] else 'warning' if eligible_consent['confirmed'] else 'pass',
        'Сведения о согласии',f'Среди технически допущенных: подтверждено источником — {eligible_consent["confirmed"]}, неизвестно — {eligible_consent["unknown"]}, отозвано — {eligible_consent["revoked"]}.')
    add('Согласия','consent_scope','pending','Объём согласия для конкретной акции','Учитываются рабочие подтверждения центра подписок и подтверждённые сведения Over-shop с рубриками, датой и происхождением. Тестовые записи не учитываются. Подтверждение ссылки без отправки письма не доказывает владение ящиком; соответствие акции условиям согласия ещё требует проверки. Регистрация, покупка и публичный email не считаются согласием. Неактивная подписка сама по себе не доказывает отзыв.')
    values={'name':'Имя получателя','company':'Компания','city':'Город','email':'recipient@example.invalid'}
    subject=template.get('subject','');preheader=template.get('preheader','');html=template.get('html','')
    diagnosis=check_template(subject,preheader,html,values)
    add('Шаблон','template','fail' if diagnosis['errors'] else 'warning' if diagnosis['warnings'] else 'pass','Проверка сохранённого письма',f'Ошибок: {diagnosis["errors"]}. Замечаний: {diagnosis["warnings"]}. Проверяется копия, сохранённая в рассылке/очереди.')
    if template.get('revision') and template['revision']!=campaign.template.revision:
        add('Шаблон','template_revision','warning','В библиотеке есть другая версия','Рассылка использует сохранённую копию. Изменения библиотеки автоматически в неё не переносятся.')
    soup=BeautifulSoup(html,'html.parser');links=[(str(tag.get(attr,'')),attr) for tag in soup.find_all(True) for attr in ('href','src') if attr in tag.attrs]
    link_errors=[];reserved=0
    for raw,attribute in links:
        if TOKEN.search(raw):continue
        issue=url_issue(raw,attribute)
        if issue:link_errors.append(issue)
        try:
            host=urlsplit(raw).hostname or ''
            if host in {'example.com','example.org','example.net','localhost'} or host.endswith('.invalid'):reserved+=1
        except ValueError:pass
    add('Ссылки','links','fail' if link_errors else 'warning' if reserved else 'pass','Адреса ссылок и изображений',f'В шаблоне адресов: {len(links)}. Некорректных статических адресов: {len(link_errors)}. Демонстрационных адресов: {reserved}. Проверяется синтаксис, без посещения сайтов и проверки доступности.')
    add('Подключения','live_unsubscribe','warning','Рабочая отписка','Веб-обработчик центра подписок подключён: открытие ссылки безопасно, отписка применяется по кнопке. Пока ссылки выдаются в центре. Подстановка {{unsubscribe_url}} в реальное письмо и доступность публичного адреса потребуют проверки при подключении отправки.')
    # Match the intended audience, not unrelated organisations sharing the mailbox.
    contact_rows=source_contacts.select_related('client','address').exclude(address=None)
    if queue:contact_rows=Contact.objects.filter(address_id__in=[r['address_id'] for r in frozen]).select_related('client','address')
    parent=dict(Audience.objects.values_list('pk','parent_id'));names=dict(Audience.objects.values_list('pk','name'))
    org_ids=set()
    for client_id,aid in Contact.objects.filter(client_id__in=contact_rows.values('client_id')).values_list('client_id','audiences__pk'):
        seen=set()
        while aid is not None and aid not in seen and parent.get(aid) is not None:seen.add(aid);aid=parent[aid]
        if names.get(aid) in {'Бизнес','Государственные структуры','Некоммерческие организации'}:org_ids.add(client_id)
    contexts=defaultdict(dict)
    for row in contact_rows.iterator(chunk_size=1000):
        email=row.address.email.strip().casefold()
        if email in technical:contexts[email][row.client_id]={'name':row.client.name,'company':row.client.name if row.client_id in org_ids else '', 'city':row.client.city,'email':email}
    tokens=[m.groups() for source in (subject,preheader,html) for m in TOKEN.finditer(source)]
    used={name for name,_ in tokens if name in values}
    missing=Counter();missing_recipients=set();ambiguous=set();subject_bad=set();dynamic_bad=set();sample=[]
    dynamic=[(raw,attribute) for raw,attribute in links if TOKEN.search(raw) and raw.strip()!='{{unsubscribe_url}}']
    for email in sorted(technical):
        choices=list(contexts.get(email,{}).values())
        if not choices:choices=[{'name':'','company':'','city':'','email':email}]
        if any(len({c.get(field,'') for c in choices})>1 for field in used):ambiguous.add(email)
        recipient_missing=set()
        for context in choices:
            for name,fallback in tokens:
                if name in values and not context[name].strip() and not (fallback or '').strip():recipient_missing.add(name)
            rendered,_=personalize(subject,context)
            if len(rendered)>250 or '\n' in rendered or '\r' in rendered:subject_bad.add(email)
            for raw,attribute in dynamic:
                rendered,_=personalize(raw,context)
                if url_issue(rendered,attribute):dynamic_bad.add(email)
        for name in recipient_missing:missing[name]+=1
        if recipient_missing:missing_recipients.add(email)
        if len(sample)<10 and (recipient_missing or email in ambiguous or email in subject_bad or email in dynamic_bad):
            sample.append({'email':email,'missing':sorted(recipient_missing),'ambiguous':email in ambiguous,'subject_error':email in subject_bad,'link_error':email in dynamic_bad})
    bad=bool(missing_recipients or ambiguous or subject_bad or dynamic_bad)
    add('Персонализация','personalization','fail' if bad else 'pass','Подстановка данных получателей',f'Без данных/запасного текста: {len(missing_recipients)}. Неоднозначные клиенты: {len(ambiguous)}. Некорректная тема: {len(subject_bad)}. Ошибки ссылок после подстановки: {len(dynamic_bad)}.')
    sender_errors=[]
    if not sender.get('name','').strip() or any(c in sender.get('name','') for c in '\r\n'):sender_errors.append('Укажите корректное имя отправителя.')
    for field in ('email','reply_to'):
        value=sender.get(field,'')
        if field=='reply_to' and not value:continue
        try:
            if any(c in value for c in '\r\n'):raise ValidationError('header')
            validate_email(value)
        except ValidationError:sender_errors.append('Некорректный email отправителя.' if field=='email' else 'Некорректный адрес для ответов.')
    add('Отправитель','sender','fail' if sender_errors else 'pass','Реквизиты отправителя',' '.join(sender_errors) if sender_errors else 'Имя, email и адрес для ответов имеют допустимый формат. Принадлежность домена этим не подтверждается.')
    add('Подключения','sender_domain','pending','Домен и аутентификация отправителя','Подтверждение домена, SPF, DKIM, DMARC и политика провайдера ещё не проверены. DNS и SMTP-запросы сейчас не выполняются.')
    schedule_bad=campaign.schedule_mode=='scheduled' and (not campaign.scheduled_at or campaign.scheduled_at<=now)
    add('Расписание','schedule','fail' if schedule_bad else 'pass','Время запуска','Указанное время уже прошло или отсутствует; требуется новая дата.' if schedule_bad else f'Ручной запуск.' if campaign.schedule_mode=='manual' else f'Запуск: {timezone.localtime(campaign.scheduled_at,ZoneInfo(campaign.schedule_timezone)).isoformat()}; часовой пояс плана {campaign.schedule_timezone}.')
    add('Подключения','transport','pending','Реальная отправка отключена','Подключена только имитация. Этот отчёт не включает отправку даже при наличии SMTP-настроек и не является гарантией попадания во «Входящие».')
    totals=Counter(c['status'] for c in checks)
    return {'campaign_id':campaign.pk,'campaign_name':campaign.name,'campaign_revision':queue.snapshot.get('campaign_revision',campaign.revision) if queue else campaign.revision,'draft_revision':campaign.revision,
            'queue_id':queue.pk if queue else None,'checked_at':now,'mode':'read_only','can_send':False,
            'verdict':'needs_changes' if totals['fail'] else 'waiting_integration',
            'summary':{key:totals[key] for key in ('pass','warning','fail','pending')},'checks':checks,
            'recipients':{'basis':'queue' if queue else 'audiences','total':len(keys),'technical_ready':len(technical),'excluded_now':len(active_keys)-len(technical),'processed':len(keys)-len(active_keys),'awaiting':len(active_keys),'current_audiences':source_counts},
            'consent':{key:evidence_counts[key] for key in ('confirmed','unknown','revoked')}|{'eligible':{key:eligible_consent[key] for key in ('confirmed','unknown','revoked')}},
            'template_findings':diagnosis['findings'],'personalization':{'missing':dict(missing),'samples':sample,'affected':len(missing_recipients),'ambiguous':len(ambiguous),'subject_errors':len(subject_bad),'link_errors':len(dynamic_bad)},
            'sender':{'name':sender.get('name',''),'email':sender.get('email',''),'reply_to':sender.get('reply_to','')},
            'note':'Срез на момент проверки. Перед реальным запуском потребуется повторная проверка. Отчёт не меняет получателей, согласия, запреты, очередь и события статистики.'}

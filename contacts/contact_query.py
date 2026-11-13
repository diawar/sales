"""Bounded server-side contact pages; all filters run before pagination."""
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Exists, OuterRef, Q, Subquery, Value, CharField, Case, When
from django.db.models.functions import Coalesce, NullIf, Cast
from .models import Contact, Evidence, Phone


SORT_FIELDS = {
    'id': 'id', 'name': 'client__name', 'email': 'address__email',
    'city': 'client__city', 'identifier': 'client__identifier',
    'validation': 'address__validation', 'industry': 'client__industry',
    'organization_type': 'client__organization_type',
}


def integer(params, key, default, minimum=1, maximum=2_147_483_647):
    try:
        value = int(params.get(key, default))
    except (ValueError, TypeError):
        raise ValueError(f'Некорректный параметр {key}.')
    if not minimum <= value <= maximum:
        raise ValueError(f'Некорректный параметр {key}.')
    return value


def filtered_contacts(params):
    rows = Contact.objects.all()
    from .segment_rules import parse_rules,validate_rules
    rules=parse_rules(params['segment_rules']) if params.get('segment_rules') else validate_rules({k:v for k,v in params.items() if k in {'$or','$exclude'}})
    if params.get('segment_rules'):
        # Saved base filters remain ANDed with any additional table filters.
        base={k:v for k,v in rules.items() if not k.startswith('$')}
        if base:rows=rows.filter(pk__in=filtered_contacts(base).order_by().values('pk'))
    if rules.get('$or'):
        union=Q()
        for group in rules['$or']:
            union|=Q(pk__in=filtered_contacts({**group,'archive':params.get('archive','0')}).order_by().values('pk'))
        rows=rows.filter(union)
    for group in rules.get('$exclude',[]):
        # Exclude the entire client even when another contact row matches.
        rows=rows.exclude(client_id__in=filtered_contacts({**group,'archive':'1'}).order_by().values('client_id'))
    completeness = params.get('completeness', '')
    if completeness not in {'', 'missing_both', 'with_email', 'with_phone'}:
        raise ValueError('Некорректный фильтр заполненности.')
    if completeness:
        # Classify the whole client, not a particular contact row.
        rows = rows.annotate(
            client_has_email=Exists(Contact.objects.filter(client_id=OuterRef('client_id'), address__isnull=False)),
            client_has_phone=Exists(Phone.objects.filter(client_id=OuterRef('client_id'))))
        if completeness == 'missing_both':
            rows = rows.filter(client_has_email=False, client_has_phone=False)
        elif completeness == 'with_email':
            rows = rows.filter(client_has_email=True)
        else:
            rows = rows.filter(client_has_phone=True)
    # SQLite LIKE is ASCII-only; preserve the browser's Cyrillic case-insensitive
    # search when filtering on the server.
    if connection.vendor == 'sqlite':
        connection.ensure_connection()
        connection.connection.create_function('CONTACT_CASEFOLD',1,lambda value: str(value or '').casefold(),deterministic=True)
        from django.db.models import Func
        from .models import Client
        folded = Client.objects.annotate(
            search_name=Func('name',function='CONTACT_CASEFOLD',output_field=CharField()),
            search_city=Func('city',function='CONTACT_CASEFOLD',output_field=CharField()),
            search_industry=Func('industry',function='CONTACT_CASEFOLD',output_field=CharField()),
            search_type=Func('organization_type',function='CONTACT_CASEFOLD',output_field=CharField()))
    if params.get('archive', '0') not in {'0', '1'}:
        raise ValueError('Некорректный параметр archive.')
    if params.get('archive') != '1':
        rows = rows.exclude(address__archived=True)
    search = params.get('query', '').strip()
    if search:
        name=Q(client__name__icontains=search)
        if connection.vendor == 'sqlite':
            name=Q(client_id__in=folded.filter(search_name__contains=search.casefold()).values('pk'))
        rows = rows.filter(name | Q(address__email__icontains=search))
    if params.get('audience'):
        audience = integer(params, 'audience', 1)
        rows = rows.filter(Q(audiences__id=audience) | Q(audiences__parent_id=audience))
    if params.get('source'):
        rows = rows.filter(Exists(Evidence.objects.filter(contact_id=OuterRef('pk'), source=params['source'])))
    if params.get('city'):
        term=params['city'].strip()
        rows = rows.filter(client_id__in=folded.filter(search_city__contains=term.casefold()).values('pk')) if connection.vendor=='sqlite' else rows.filter(client__city__icontains=term)
    if params.get('profile_type'):
        term = params['profile_type'].strip()
        rows = rows.filter(client_id__in=folded.filter(Q(search_industry__contains=term.casefold())|Q(search_type__contains=term.casefold())).values('pk')) if connection.vendor=='sqlite' else rows.filter(Q(client__industry__icontains=term)|Q(client__organization_type__icontains=term))
    if params.get('profile_tag'):
        # Read only the small tag arrays, avoiding backend-specific JSON text
        # encodings (SQLite stores Cyrillic using escaped Unicode).
        from .models import Client
        term=params['profile_tag'].lstrip('#').strip().casefold()
        matching=[pk for pk,tags in Client.objects.values_list('pk','tags').iterator(chunk_size=500)
                  if term in ' '.join(tags).casefold()]
        rows=rows.filter(client_id__in=matching)
    if params.get('validation'):
        value = params['validation']
        rows = rows.filter(address__isnull=True) if value == 'Без email' else rows.filter(address__validation=value)
    if params.get('email_domain', '').strip():
        from django.core.validators import validate_email
        from django.core.exceptions import ValidationError
        domain=params['email_domain'].strip().lstrip('@').lower()
        try:
            domain=domain.encode('idna').decode('ascii')
            validate_email('check@'+domain)
        except (UnicodeError,ValidationError):
            raise ValueError('Укажите почтовый домен, например mail.ru.')
        if '@' in domain or len(domain)>253:
            raise ValueError('Укажите почтовый домен, например mail.ru.')
        rows=rows.filter(address__email__iendswith='@'+domain)
    from .email_policy import blocked_query, eligible_addresses
    from .models import Address
    excluded = Q(address__isnull=False) & blocked_query('address__')
    send_status = params.get('send_status', '')
    if send_status == 'ready':
        rows = rows.filter(address_id__in=eligible_addresses(Address.objects.all()).values('pk'))
    elif send_status == 'excluded':
        rows = rows.filter(excluded)
    elif send_status == 'pending':
        rows = rows.exclude(excluded).exclude(address_id__in=eligible_addresses(Address.objects.all()).values('pk'))
    elif send_status:
        raise ValueError('Некорректный фильтр отправки.')
    activity = params.get('activity')
    if activity == 'Исключены':
        rows = rows.filter(excluded)
    elif activity == 'Мало данных':
        rows = rows.exclude(excluded)
    elif activity in {'Активные', 'Пассивные'}:
        rows = rows.none()  # Delivery events are not connected yet.
    elif activity:
        raise ValueError('Некорректная активность.')
    for metric in ('delivered', 'clicks'):
        if params.get(metric) and integer(params, metric, 0, minimum=0) > 0:
            rows = rows.none()
    kind = params.get('phone_kind', '')
    if kind and kind not in {'mobile', 'landline', 'service', 'unknown'}:
        raise ValueError('Некорректный тип телефона.')
    # Each phone criterion may match a different number of the same client,
    # matching the existing table filters.
    if kind:
        rows = rows.filter(Exists(Phone.objects.filter(client_id=OuterRef('client_id'), kind=kind)))
    number = ''.join(params.get('phone', '').split())
    if number:
        rows = rows.filter(Exists(Phone.objects.filter(client_id=OuterRef('client_id'), number__contains=number)))
    if params.get('ids'):
        parts=params['ids'].split(',')
        if len(parts)>10000 or any(not p.isascii() or not p.isdigit() for p in parts):
            raise ValueError('Некорректный список контактов.')
        rows=rows.filter(pk__in=[int(p) for p in parts])
    sort = params.get('sort', 'name')
    fields=dict(SORT_FIELDS)
    if sort=='source':
        rows=rows.annotate(sort_value=Coalesce(Subquery(Evidence.objects.filter(contact_id=OuterRef('pk')).order_by('id').values('source')[:1]),Value('')))
        fields[sort]='sort_value'
    elif sort=='audience':
        from .models import Audience
        rows=rows.annotate(sort_value=Coalesce(Subquery(Audience.objects.filter(Q(contact__pk=OuterRef('pk'))|Q(children__contact__pk=OuterRef('pk'))).order_by('name').values('name')[:1]),Value('')))
        fields[sort]='sort_value'
    elif sort=='phone':
        rows=rows.annotate(sort_value=Coalesce(Subquery(Phone.objects.filter(client_id=OuterRef('client_id')).order_by('number').values('number')[:1]),Value('')))
        fields[sort]='sort_value'
    elif sort=='profile_type':
        rows=rows.annotate(sort_value=Coalesce(NullIf('client__industry',Value('')),'client__organization_type'))
        fields[sort]='sort_value'
    elif sort=='profile_tag':
        rows=rows.annotate(sort_value=Cast('client__tags',CharField()))
        fields[sort]='sort_value'
    elif sort=='activity':
        rows=rows.annotate(sort_value=Case(When(excluded,then=Value('Исключены')),default=Value('Мало данных'),output_field=CharField()))
        fields[sort]='sort_value'
    elif sort in {'delivered','opens','clicks'}:
        # All values are zero until delivery events are connected.
        fields[sort]='id'
    if sort not in fields:
        raise ValueError('Недопустимое поле сортировки.')
    direction = params.get('direction', 'asc')
    if direction not in {'asc', 'desc'}:
        raise ValueError('Некорректное направление сортировки.')
    field=fields[sort]
    if sort in {'delivered','opens','clicks'}:
        return rows.distinct().order_by('id')
    return rows.distinct().order_by(('-' if direction == 'desc' else '') + field, 'id')


def contact_page(params):
    page = integer(params, 'page', 1)
    size = integer(params, 'page_size', 20, maximum=100)
    rows=filtered_contacts(params)
    unique_emails=rows.order_by().exclude(address__isnull=True).values('address_id').distinct().count()
    paginator = Paginator(rows, size)
    # Clamp after filtering or removal so a stale page still shows results.
    current = paginator.page(min(page, paginator.num_pages))
    return current.object_list, {
        'page': current.number, 'page_size': size, 'pages': paginator.num_pages,
        'total': paginator.count, 'has_next': current.has_next(),
        'has_previous': current.has_previous(),
        'unique_emails': unique_emails,
    }

"""Transport-independent eligibility. DNS success never establishes consent or mailbox existence."""
from django.db.models import Q

REASONS = {'unsubscribe': 'Отписка', 'complaint': 'Жалоба', 'hard_bounce': 'Окончательная ошибка доставки', 'manual': 'Ручной запрет'}
BAD_CODES = ('syntax', 'null_mx', 'nxdomain', 'no_route')

def blocked_query(prefix=''):
    return (Q(**{prefix+'archived': True}) | ~Q(**{prefix+'suppression_reason': ''}) |
            (Q(**{prefix+'validation_result__has_key': 'code'}) & Q(**{prefix+'validation_result__code__in': BAD_CODES})))

def eligible_addresses(queryset):
    # Reuse immediately before transport handoff; do not rely on an old audience snapshot.
    return queryset.filter(archived=False, suppression_reason='', validation_result__code__in=['mx', 'implicit_mx'])

def sending_state(address):
    if not address: return {'code': 'missing', 'label': 'Без email', 'reason': 'Email не указан.'}
    if address.archived: return {'code': 'excluded', 'label': 'Исключён', 'reason': 'Адрес в архиве.'}
    if address.suppression_reason: return {'code': 'excluded', 'label': 'Исключён', 'reason': address.suppression_reason}
    code = address.validation_result.get('code')
    if code in BAD_CODES: return {'code': 'invalid', 'label': 'Ошибка email', 'reason': address.validation_result.get('reason', address.validation)}
    if code not in ('mx', 'implicit_mx'): return {'code': 'pending', 'label': 'Нужна проверка', 'reason': 'Нет достоверного результата DNS. Отправка приостановлена.'}
    return {'code': 'ready', 'label': 'DNS проверен', 'reason': 'Почтовый маршрут есть. Существование ящика и согласие не подтверждены.'}

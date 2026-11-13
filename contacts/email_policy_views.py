from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import Contact, Address, EmailExclusion
from .views import body, error
from .email_policy import REASONS, sending_state


def payload(address):
    return {'email': address.email, 'sending': sending_state(address), 'checked_at': address.validation_checked_at,
            'validation': address.validation, 'result': address.validation_result,
            'history': [{'id': e.pk, 'reason': e.reason, 'label': REASONS.get(e.reason, e.reason), 'note': e.note,
                         'created_at': e.created_at, 'removed_at': e.removed_at, 'removal_note': e.removal_note}
                        for e in address.exclusions.order_by('-created_at')[:100]]}


@require_http_methods(['GET', 'POST'])
def address_status(request, contact_id):
    try: contact = Contact.objects.select_related('address').get(pk=contact_id)
    except Contact.DoesNotExist: return error('Контакт не найден.', 404)
    if not contact.address_id: return error('У клиента нет email.')
    if request.method == 'GET': return JsonResponse(payload(contact.address))
    data = body(request)
    action = data.get('action')
    note = data.get('note', '')
    if not isinstance(note, str) or len(note) > 500: return error('Комментарий — до 500 символов.')
    if action == 'recheck':
        # The background queue performs DNS, never a synchronous HTTP request.
        Address.objects.filter(pk=contact.address_id).update(validation_checked_at=None, validation_next_at=None,
            validation='Не проверен', validation_result={}, validation_attempts=0)
    elif action in ('exclude', 'restore'):
        reason = data.get('reason')
        if not isinstance(reason, str) or reason not in REASONS: return error('Выберите причину исключения.')
        if action == 'restore' and not note.strip(): return error('Укажите основание снятия запрета; для отписки — новое согласие.')
        with transaction.atomic():
            address = Address.objects.select_for_update().get(pk=contact.address_id)
            if action=='restore' and reason=='unsubscribe':
                from .subscriptions import has_revocation
                if has_revocation(address.email):return error('Согласие отозвано в центре подписок. Сначала нужно новое подтверждение по ссылке.',409)
            active = list(address.exclusions.filter(removed_at__isnull=True))
            # Preserve older/imported restrictions even when adding or removing another reason.
            known = '; '.join(REASONS[e.reason] for e in sorted(active, key=lambda e: e.reason))
            legacy = address.suppression_reason if address.suppression_reason != known else ''
            if legacy: return error('Есть прежний запрет: требуется отдельная проверка источника.', 409)
            if action == 'exclude':
                EmailExclusion.objects.get_or_create(address=address, reason=reason, removed_at=None, defaults={'note': note.strip()})
            else:
                address.exclusions.filter(reason=reason, removed_at__isnull=True).update(removed_at=timezone.now(), removal_note=note.strip())
            labels = [REASONS[e.reason] for e in address.exclusions.filter(removed_at__isnull=True).order_by('reason')]
            address.suppression_reason = '; '.join(([legacy] if legacy else []) + labels)[:100]
            address.save(update_fields=['suppression_reason'])
    else: return error('Неизвестное действие.')
    return JsonResponse(payload(Address.objects.get(pk=contact.address_id)))

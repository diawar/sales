from django.db import IntegrityError,transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from .models import SavedSegment
from .contact_query import filtered_contacts
from .views import body,error
from .segment_rules import validate_rules

FILTERS={'query','audience','activity','validation','source','delivered','clicks','archive','phone_kind','phone','city','profile_type','profile_tag','completeness','email_domain'}


@require_http_methods(['GET','POST'])
def segments(request):
    if request.method=='GET':
        return JsonResponse({'segments':list(SavedSegment.objects.filter(archived=False).order_by('name').values('id','name','filters','updated_at'))})
    payload=body(request);action=payload.get('action','save')
    try:
        if action not in {'save','archive','restore','preview'}:raise ValueError('Некорректное действие.')
        segment=None
        if payload.get('id') is not None:
            if type(payload['id']) is not int:raise ValueError('Некорректный сегмент.')
            segment=SavedSegment.objects.get(pk=payload['id'])
        if action in {'archive','restore'}:
            if not segment:raise ValueError('Выберите сегмент.')
            segment.archived=action=='archive';segment.save(update_fields=['archived','updated_at'])
            return JsonResponse({'id':segment.pk,'archived':segment.archived})
        criteria=payload.get('filters',segment.filters if segment else {})
        criteria=validate_rules(criteria)
        rows=filtered_contacts(criteria)
        # Evaluate the same query used by pages and export before saving.
        counts={'contacts':rows.count(),'clients':rows.order_by().values('client_id').distinct().count(),
            'emails':rows.order_by().exclude(address__isnull=True).values('address_id').distinct().count()}
        if action=='preview':return JsonResponse({'filters':criteria,'counts':counts})
        name=payload.get('name','')
        if not isinstance(name,str) or not 1<=len(name.strip())<=120:raise ValueError('Название должно содержать от 1 до 120 символов.')
        with transaction.atomic():
            segment=segment or SavedSegment()
            segment.name=name.strip();segment.name_key=segment.name.casefold();segment.filters=criteria;segment.save()
        return JsonResponse({'id':segment.pk,'name':segment.name,'filters':segment.filters,'counts':counts})
    except SavedSegment.DoesNotExist:return error('Сегмент не найден.',404)
    except IntegrityError:return error('Сегмент с таким названием уже существует.')
    except ValueError as exc:return error(str(exc))

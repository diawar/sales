import json
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from contacts.models import Address
from contacts.email_validation import check_email


class Command(BaseCommand):
    help='Ограниченная проверка синтаксиса email и DNS без SMTP и архивирования.'

    def add_arguments(self,parser):
        parser.add_argument('--limit',type=int,default=20)

    def handle(self,*args,**options):
        limit=options['limit']
        if not 1<=limit<=100:raise CommandError('Лимит от 1 до 100 адресов.')
        cache={};counts={}
        for address in Address.objects.filter(validation_checked_at__isnull=True,archived=False,suppression_reason='').order_by('id')[:limit]:
            result=check_email(address.email,cache)
            # Do not undo an exclusion or another worker's result.
            Address.objects.filter(pk=address.pk,validation_checked_at__isnull=True,archived=False,suppression_reason='').update(
                validation=result['status'],validation_result=result,validation_checked_at=timezone.now())
            counts[result['code']]=counts.get(result['code'],0)+1
        self.stdout.write(json.dumps({'checked':sum(counts.values()),'results':counts,'domains':len(cache)}))

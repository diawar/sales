from django.core.management.base import BaseCommand,CommandError
from contacts.automation_simulation import tick


class Command(BaseCommand):
    help='Один цикл имитации сценариев. Никогда не отправляет письма и не меняет реальных клиентов.'

    def add_arguments(self,parser):
        parser.add_argument('--advance-minutes',type=int,default=0,help='Сдвиг общих часов имитации, 0–10080 минут.')

    def handle(self,*args,**options):
        try:count=tick(options['advance_minutes'])
        except ValueError as exc:raise CommandError(str(exc))
        self.stdout.write(f'Имитация: {count} шагов. Реальная отправка отключена.')

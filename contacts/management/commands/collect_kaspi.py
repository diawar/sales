import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from contacts.kaspi_collection import run_round
from contacts.models import KaspiCollection

class Command(BaseCommand):
    help='Автоматический сбор публичных продавцов компьютеров/комплектующих Kaspi. Управление в интерфейсе.'
    def add_arguments(self,parser):parser.add_argument('--once',action='store_true')
    def handle(self,*args,**options):
        self.stdout.write('Kaspi collector ready; one public request per step, minimum 10 seconds between requests.')
        while True:
            close_old_connections()
            try:run_round()
            except Exception:
                # Unknown errors require operator review instead of an unbounded retry loop.
                KaspiCollection.objects.filter(pk=1).update(enabled=False,error='Внутренняя ошибка обработчика Kaspi. Очередь сохранена; требуется проверка.')
                self.stderr.write('Kaspi paused; progress retained. Check local logs and configuration.')
            if options['once']:return
            time.sleep(2)

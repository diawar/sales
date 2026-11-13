import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from contacts.dispatch_queue import run_round


class Command(BaseCommand):
    help='Обработка очередей рассылок ТОЛЬКО В ИМИТАЦИИ, без SMTP.'
    def add_arguments(self,parser): parser.add_argument('--once',action='store_true')
    def handle(self,*args,**options):
        self.stdout.write('Campaign queue worker: SIMULATION ONLY. No messages will be sent.')
        while True:
            close_old_connections()
            try: run_round()
            except Exception as exc:
                self.stderr.write('Queue round failed: '+type(exc).__name__)
            if options['once']: return
            time.sleep(.25)

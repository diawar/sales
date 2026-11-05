import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from contacts.email_queue import run_round


class Command(BaseCommand):
    help='Очередь проверки email с управлением запуском и паузой.'
    def add_arguments(self,parser):parser.add_argument('--once',action='store_true')
    def handle(self,*args,**options):
        self.stdout.write('Email validation worker ready.')
        while True:
            close_old_connections()
            try:run_round()
            except Exception:self.stderr.write('Email validation database unavailable.')
            if options['once']:return
            time.sleep(2)

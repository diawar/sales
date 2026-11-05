import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from contacts.gov_collection import run_round

class Command(BaseCommand):
    help='Collect configured public Gov.kz cards; controlled from the interface.'
    def add_arguments(self,parser):parser.add_argument('--once',action='store_true')
    def handle(self,*args,**options):
        self.stdout.write('Gov.kz collector ready.')
        while True:
            close_old_connections()
            try:run_round()
            except Exception:self.stderr.write('Gov.kz worker unavailable; progress retained.')
            if options['once']:return
            time.sleep(2)

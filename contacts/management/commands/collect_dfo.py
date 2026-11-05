import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from contacts.dfo_collection import run_round

class Command(BaseCommand):
    help='Public DFO collection, controlled from the interface.'
    def add_arguments(self,parser):parser.add_argument('--once',action='store_true')
    def handle(self,*args,**options):
        self.stdout.write('DFO worker ready.')
        while True:
            close_old_connections()
            try:run_round()
            except Exception:self.stderr.write('DFO worker unavailable; cursor retained.')
            if options['once']:return
            time.sleep(2)

import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from contacts.producers import run_round

class Command(BaseCommand):
    help='Run automatic e-ondiris collection; enable or pause in the interface.'
    def add_arguments(self,parser):parser.add_argument('--once',action='store_true')
    def handle(self,*args,**options):
        self.stdout.write('Automatic producer collector ready.')
        while True:
            close_old_connections()
            try:run_round()
            except Exception:self.stderr.write('Preview worker unavailable; progress retained.')
            if options['once']:return
            time.sleep(2)

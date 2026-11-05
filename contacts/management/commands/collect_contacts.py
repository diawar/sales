import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from contacts.collection import run_round

class Command(BaseCommand):
    help='Run the independent public contact collector. Enable/pause in the interface.'
    def add_arguments(self,parser):parser.add_argument('--once',action='store_true')
    def handle(self,*args,**options):
        self.stdout.write('Contact collector ready. Enable or pause collection in the interface.')
        while True:
            close_old_connections()
            try:changed=run_round()
            except Exception:
                changed=False;self.stderr.write('Collector unavailable; progress retained. Check application logs.')
            if options['once']:return
            time.sleep(2 if changed else 5)

"""Controlled acceptance run: save one page then pause, preserving cursor."""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','mailer.settings')
import django
django.setup()
from contacts.producers import control,run_round
from contacts.models import ProducerPilot
control('start')
try:
    done=run_round()
finally:
    control('pause')
state=ProducerPilot.objects.get(pk=1)
print({'page_saved':done,'status':state.status,'next_page':state.next_page,'error':state.error,'report':state.report})

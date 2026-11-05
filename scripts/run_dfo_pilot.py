"""Authorized two-page DFO acceptance pilot; always pause when leaving."""
import os
import time
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','mailer.settings')
import django
django.setup()
from contacts.dfo_collection import control,run_round
from contacts.models import DfoCollection
from django.utils import timezone

if __name__=='__main__':
    state,_=DfoCollection.objects.get_or_create(pk=1)
    if state.enabled:raise RuntimeError('Pause the existing collector before running a pilot.')
    control('start',mode='pilot')
    try:
        for _ in range(2):
            state=DfoCollection.objects.get(pk=1)
            if not state.enabled:break
            if state.next_attempt_at:
                delay=(state.next_attempt_at-timezone.now()).total_seconds()
                if delay>30:raise RuntimeError('Pilot in cooldown; retain cursor and stop.')
                if delay>0:time.sleep(delay+0.1)
            if not run_round():break
        state=DfoCollection.objects.get(pk=1)
        print(json.dumps({'report':state.report,'error':state.error,'next_page':state.next_page,'completed':bool(state.completed_at)},ensure_ascii=True),flush=True)
    finally:control('pause')

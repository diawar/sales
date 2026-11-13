"""Local acceptance fingerprints. Outputs counts and digests, never client records or secrets."""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));os.environ.setdefault('DJANGO_SETTINGS_MODULE','mailer.settings')
import django
django.setup()
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

MODELS=['Audience','Client','Contact','Phone','Evidence','ClientFact','OverShopSubscription','EmailSubscription',
    'EmailExclusion','EmailTemplate','CampaignSender','EmailCampaign','CampaignApproval','AccountRole','NotificationRead']

def fingerprint(query):
    h=hashlib.sha256();count=0
    for row in query.iterator(chunk_size=1000):
        h.update(json.dumps(row,cls=DjangoJSONEncoder,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode());h.update(b'\n');count+=1
    return {'count':count,'sha256':h.hexdigest()}

def snapshot():
    with transaction.atomic():
        result={name:fingerprint(apps.get_model('contacts',name).objects.order_by('pk').values()) for name in MODELS}
        result['Address']=fingerprint(apps.get_model('contacts','Address').objects.order_by('pk').values('id','email','archived','suppression_reason'))
        for name,field in [('Contact','audiences'),('EmailCampaign','audiences')]:
            through=getattr(apps.get_model('contacts',name),field).through
            result[name+'.'+field]=fingerprint(through.objects.order_by('pk').values())
        result['Users']=fingerprint(get_user_model().objects.order_by('pk').values('id','username','is_superuser','is_staff','is_active'))
    return {'captured_at':timezone.now().isoformat(),'tables':result}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--compare',type=Path)
    args=parser.parse_args();data=snapshot()
    if args.compare:
        before=json.loads(args.compare.read_text(encoding='utf-8'))['tables']
        data['changed_tables']=[name for name,value in data['tables'].items() if before.get(name)!=value]
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'tables':len(data['tables']),'changed_tables':data.get('changed_tables'),'report':str(args.output)},ensure_ascii=False))

import json
import hashlib
import sqlite3
import uuid
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.db import connection
from contacts.overshop_source import prepare_user_csv,parse_subscribers_csv
from contacts.overshop_sync import import_snapshot


class Command(BaseCommand):
    help='Проверить и импортировать выгрузки аккаунтов и подписок Over-shop. Без --commit только проверка.'
    def add_arguments(self,parser):
        parser.add_argument('--customers',required=True)
        parser.add_argument('--subscribers',required=True)
        parser.add_argument('--commit',action='store_true')
    def handle(self,*args,**options):
        try:
            raw=Path(options['customers']).read_bytes();subraw=Path(options['subscribers']).read_bytes()
            if len(raw)>30_000_000 or len(subraw)>10_000_000:raise ValueError('Слишком большая выгрузка.')
            customers=prepare_user_csv(raw.decode('utf-8-sig'))
            subscribers=parse_subscribers_csv(subraw.decode('utf-8-sig'))
            if not subscribers:raise ValueError('Пустая выгрузка подписок требует отдельной проверки.')
            digest=hashlib.sha256(raw+b'\0'+subraw).hexdigest()
            if not options['commit']:
                self.stdout.write(json.dumps({'accounts':len(customers),'subscriptions':len(subscribers),'committed':False}))
                return
            if connection.vendor=='sqlite':
                folder=settings.BASE_DIR/'data/backups';folder.mkdir(parents=True,exist_ok=True)
                backup=folder/('before-overshop-'+uuid.uuid4().hex+'.sqlite3')
                with sqlite3.connect(str(connection.settings_dict['NAME'])) as source,sqlite3.connect(str(backup)) as target:
                    source.backup(target)
                    if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Ошибка резервной копии.')
            result=import_snapshot(customers,subscribers,digest)
            folder=settings.BASE_DIR/'data/overshop';folder.mkdir(parents=True,exist_ok=True)
            (folder/'full-import-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            from collections import Counter
            summary={'records':len(customers),'categories':dict(Counter(r['category'] for r in customers)),
                     'candidate_purchases':dict(Counter(r['purchase_segment'] for r in customers if r['category']=='person_candidate'))}
            (folder/'full-review.json').write_text(json.dumps({'summary':summary,'records':customers},ensure_ascii=False),encoding='utf-8')
            self.stdout.write(json.dumps(result,ensure_ascii=True))
        except (OSError,ValueError,KeyError,TypeError) as exc:
            raise CommandError(str(exc))

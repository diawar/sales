import hashlib
import os
import secrets
from datetime import timedelta
from pathlib import Path
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from django.utils import timezone
from contacts.accounts import lock_gate


class Command(BaseCommand):
    help='Подготовить одноразовую настройку первой учётной записи без вывода кода в консоль.'
    def handle(self,*args,**options):
        with transaction.atomic():
            gate=lock_gate()
            if get_user_model().objects.exists():raise CommandError('Учётные записи уже существуют. Первая настройка закрыта.')
            raw=secrets.token_urlsafe(32)
            gate.setup_digest=hashlib.sha256(raw.encode()).hexdigest();gate.setup_expires_at=timezone.now()+timedelta(hours=24);gate.save()
            folder=Path(settings.DATA_DIR)/'private';folder.mkdir(parents=True,exist_ok=True)
            path=folder/'account-setup-code.txt'
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            with os.fdopen(fd,'w',encoding='utf-8') as stream:stream.write(raw+'\n')
        self.stdout.write('Одноразовый код сохранён в приватном файле: '+str(path))
        self.stdout.write('Откройте /setup/ и создайте первую учётную запись. Код действует 24 часа и не публикуется в Git.')

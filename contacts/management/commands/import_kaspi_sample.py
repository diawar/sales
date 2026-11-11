import json
from pathlib import Path
from django.core.management.base import BaseCommand,CommandError
from contacts.kaspi_pilot import parse_samples,import_samples

class Command(BaseCommand):
    help='Пилот из максимум 10 наблюдённых публичных карточек Kaspi; без --commit только проверка.'
    def add_arguments(self,parser):
        parser.add_argument('file')
        parser.add_argument('--commit',action='store_true')
    def handle(self,*args,**options):
        try:
            raw=Path(options['file']).read_bytes()
            if len(raw)>10_000_000:raise ValueError('Файл больше 10 МБ.')
            samples=json.loads(raw)
            result=import_samples(samples) if options['commit'] else {'merchants':len(parse_samples(samples)),'committed':False}
            self.stdout.write(json.dumps(result,ensure_ascii=True))
        except (OSError,ValueError,KeyError,TypeError) as exc:raise CommandError(str(exc))

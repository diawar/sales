"""Limited read-only live check. Does not write any contact or collector state."""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','mailer.settings')
import django
django.setup()
from datetime import timedelta
from django.utils import timezone
from contacts.goszakup_client import PublicClient,SourceError
from contacts.goszakup_source import LeadPortal

now=timezone.now()
try:
    with PublicClient() as api:
        page=api.search(system_id__in='1__4',results_publication_date__gte=(now-timedelta(days=7)).isoformat(),results_publication_date__lte=now.isoformat(),ordering='-publish_date',limit=1,offset=0)
        print({'matching_results':page.count,'sample_rows':len(page.results)})
        if page.results:
            row=page.results[0]
            people,unresolved,url=LeadPortal(api).protocol(row['external_id'])
            print({'participants':len(people),'unresolved':unresolved,'protocol_host':url.split('/')[2]})
            if people:
                value=LeadPortal(api).contacts(people[0].identifier)
                print({'email_count':len(value['emails']),'phone_count':len(value.get('phones',[])),'registry_matched':True})
except SourceError as exc:
    print('Source check:',str(exc))
    sys.exit(1)

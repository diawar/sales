"""Read a consistent in-memory SQLite snapshot; never change customer records."""
import itertools
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = sqlite3.connect((ROOT / 'data/contacts.sqlite3').as_uri() + '?mode=ro', uri=True)
db = sqlite3.connect(':memory:')
source.backup(db)
source.close()
db.row_factory = sqlite3.Row
rows = lambda sql: list(db.execute(sql))
clients = rows('SELECT * FROM contacts_client')
ids = {r['id'] for r in clients}
emails = {r['client_id'] for r in rows('SELECT DISTINCT client_id FROM contacts_contact WHERE address_id IS NOT NULL')}
phones = {r['client_id'] for r in rows('SELECT DISTINCT client_id FROM contacts_phone')}
sources = defaultdict(set)
for r in rows('SELECT c.client_id,e.source FROM contacts_evidence e JOIN contacts_contact c ON c.id=e.contact_id UNION SELECT client_id,source FROM contacts_clientfact UNION SELECT client_id,source FROM contacts_phone'):
    sources[r['source']].add(r['client_id'])
bins = defaultdict(list)
for r in clients:
    if r['identifier']: bins[r['identifier']].append(r['id'])
audiences = {r['id']: dict(r) for r in rows('SELECT id,name,parent_id FROM contacts_audience')}
members = defaultdict(set)
for r in rows('SELECT c.client_id,ca.audience_id FROM contacts_contact c JOIN contacts_contact_audiences ca ON ca.contact_id=c.id'):
    aid = r['audience_id']
    visited = set()
    while aid in audiences and aid not in visited:
        visited.add(aid)
        members[aid].add(r['client_id'])
        aid = audiences[aid]['parent_id']
shared = rows('SELECT address_id,COUNT(DISTINCT client_id) n FROM contacts_contact WHERE address_id IS NOT NULL GROUP BY address_id HAVING COUNT(DISTINCT client_id)>1')
report = {
    'snapshot_utc': datetime.now(timezone.utc).isoformat(),
    'clients': len(ids), 'contact_rows': rows('SELECT COUNT(*) n FROM contacts_contact')[0]['n'],
    'unique_emails': rows('SELECT COUNT(*) n FROM contacts_address')[0]['n'],
    'with_email': len(emails), 'with_phone': len(phones),
    'with_both': len(emails & phones), 'without_email_or_phone': len(ids - emails - phones),
    'with_city': sum(bool(r['city'].strip()) for r in clients),
    'with_identifier': sum(bool(r['identifier']) for r in clients),
    'duplicate_identifiers': {k:v for k,v in bins.items() if len(v)>1},
    'invalid_identifier_client_ids': [r['id'] for r in clients if r['identifier'] and not re.fullmatch(r'[0-9]{12}',r['identifier'])],
    'identity_mismatch_client_ids': [r['id'] for r in clients if r['identifier'] and r['identity_key'] != 'kz:'+r['identifier']],
    'shared_email_addresses': len(shared),
    'phone_kinds': dict(Counter(r['kind'] for r in rows('SELECT kind FROM contacts_phone'))),
    'sources': {k:len(v) for k,v in sorted(sources.items())},
    'source_intersections': [{'a':a,'b':b,'clients':len(sources[a]&sources[b])} for a,b in itertools.combinations(sorted(sources),2) if sources[a]&sources[b]],
    'audiences': [{'id':aid,'name':a['name'],'parent_id':a['parent_id'],'clients':len(members[aid])} for aid,a in audiences.items()],
    'without_audience': len(ids-set().union(*members.values())) if members else len(ids),
    'dfo': [dict(r) for r in rows('SELECT enabled,mode,next_page,attempts,error,heartbeat_at,next_attempt_at FROM contacts_dfocollection')],
}
out = ROOT / 'docs/CONTACT_BASE_AUDIT.json'
out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True,indent=2))

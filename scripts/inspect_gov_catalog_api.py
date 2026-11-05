"""Inspect two public, homepage-referenced bundles, without executing them."""
from pathlib import Path
import re,json
import httpx
for name in ['OrganizationsContainer.DjDRctDA.chunk.js','organizationsApi.8IGeKzGT.chunk.js','parseRawCurators.CmXEfcF8.chunk.js']:
    r=httpx.get('https://www.gov.kz/static/js/'+name,timeout=30)
    r.raise_for_status()
    if len(r.content)>5_000_000:raise ValueError('Too large')
    Path('data/gov-inspection/'+name).write_text(r.text,encoding='utf-8')
    print(name)
    for m in list(re.finditer(r'getOrganizations|organizations|entities|baseUrl|/api/|maslihat|ministr|query|curator',r.text))[:45]:
        print(json.dumps(r.text[max(0,m.start()-100):m.end()+220],ensure_ascii=True))

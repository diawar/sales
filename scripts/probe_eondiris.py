"""One bounded request to the public endpoint used by the registry frontend."""
import json
from pathlib import Path
import httpx
url='https://e-ondiris.gov.kz/awp-api/registry-front'
with httpx.Client(timeout=30,follow_redirects=False) as client:
    response=client.get(url,params={'page':1,'limit':20})
    print('HTTP',response.status_code)
    response.raise_for_status()
    payload=response.json()
    root=Path(__file__).resolve().parent.parent/'data'/'eondiris-inspection'
    root.mkdir(parents=True,exist_ok=True)
    (root/'page-1.json').write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
    print('Keys:',list(payload))
    print('Meta:',payload.get('meta'))
    rows=payload.get('data',[])
    print('Rows:',len(rows),'Row keys:',list(rows[0]) if rows else [])

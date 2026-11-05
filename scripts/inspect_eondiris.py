"""Read public frontend metadata only; bounded requests, no authentication."""
from pathlib import Path
from urllib.parse import urljoin,urlsplit
import re
import httpx
root=Path(__file__).resolve().parent.parent/'data'/'eondiris-inspection'
root.mkdir(parents=True,exist_ok=True)
with httpx.Client(timeout=25,follow_redirects=False) as client:
    for path in ['/', '/robots.txt']:
        response=client.get('https://e-ondiris.gov.kz'+path)
        print(path,response.status_code)
        if response.status_code==200:
            (root/('index.html' if path=='/' else 'robots.txt')).write_text(response.text,encoding='utf-8')
        if path=='/' and response.status_code==200:
            scripts=re.findall(r'<script[^>]+src=[\"\x27]([^\"\x27]+)',response.text)
            print('Scripts:',scripts[:5])
            for index,src in enumerate(scripts[:3]):
                url=urljoin('https://e-ondiris.gov.kz',src)
                if urlsplit(url).netloc!='e-ondiris.gov.kz':continue
                asset=client.get(url)
                if asset.status_code==200 and len(asset.content)<5*1024*1024:
                    (root/f'asset-{index}.js').write_text(asset.text,encoding='utf-8')
                    matches=re.findall(r'[\"\x27`]([^\"\x27`\s]{0,160}(?:api|registry|reestr)[^\"\x27`\s]{0,160})[\"\x27`]',asset.text,re.I)
                    print('Endpoint candidates:',matches[:35])

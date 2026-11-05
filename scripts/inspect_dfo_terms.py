"""Download only the published DFO agreement and read its text locally."""
from io import BytesIO
from pathlib import Path
import sys
import httpx
from pypdf import PdfReader

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    response=httpx.get('https://dfo.kz/files/dfo/agreement.pdf',timeout=30,follow_redirects=False)
    print('HTTP',response.status_code,'bytes',len(response.content),flush=True)
    response.raise_for_status()
    if len(response.content)>4_000_000 or not response.content.startswith(b'%PDF'):
        raise ValueError('Expected bounded PDF agreement')
    folder=Path('data/dfo-inspection');folder.mkdir(parents=True,exist_ok=True)
    folder.joinpath('agreement.pdf').write_bytes(response.content)
    reader=PdfReader(BytesIO(response.content))
    text='\n\n'.join(f'СТРАНИЦА {i+1}\n'+(p.extract_text() or '') for i,p in enumerate(reader.pages))
    folder.joinpath('agreement.txt').write_text(text,encoding='utf-8')
    print(text)

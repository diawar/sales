"""One read-only request to the documented KGD endpoint; no credentials or DB writes."""
import json
from pathlib import Path

import httpx

URL = 'https://portal.kgd.gov.kz/services/isnaportalsync/public/taxpayer-data'

if __name__ == '__main__':
    result = {'url': URL, 'authenticated': False, 'database_writes': False}
    try:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            with client.stream('GET', URL, params={
                'taxpayerCode': '971240001315', 'taxpayerType': 'UL', 'print': 'false',
            }) as response:
                result.update(status=response.status_code,
                              content_type=response.headers.get('content-type'),
                              retry_after=response.headers.get('retry-after'))
                size = 0
                error_body = bytearray()
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if response.status_code >= 400 and len(error_body) < 4096:
                        error_body.extend(chunk[:4096 - len(error_body)])
                    if size > 1_000_000:
                        result['truncated'] = True
                        break
                result['bytes_read'] = size
                if error_body:
                    result['error_body'] = error_body.decode('utf-8', errors='replace')
    except httpx.HTTPError as error:
        result['error'] = type(error).__name__
    folder = Path('data/kgd-inspection')
    folder.mkdir(parents=True, exist_ok=True)
    folder.joinpath('access-probe.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))

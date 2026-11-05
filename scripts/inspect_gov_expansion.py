from pathlib import Path
import sys
import httpx
from bs4 import BeautifulSoup
slug=sys.argv[1] if len(sys.argv)>1 else 'almaty'
if slug not in {'almaty','moa','dsm','energo','economy','antiterrosticheskiy-centr','glubokoe-maslihat','afm','maslihat-pavlodar-aksu'}:raise ValueError('Not in bounded sample')
response=httpx.get(f'https://www.gov.kz/org/{slug}/contacts?lang=ru',timeout=30,follow_redirects=False)
response.raise_for_status()
if len(response.content)>4_000_000:raise ValueError('Too large')
Path(f'data/gov-inspection/{slug}-contacts.html').write_text(response.text,encoding='utf-8')
soup=BeautifulSoup(response.text,'html.parser')
print('HEADINGS',str(soup.select('.ministry-header-top__title, h1'))[:1200].encode('ascii','backslashreplace').decode())
for block in soup.select('.contacts-detail, .ministry-contacts-card .contacts.inner-html'):
    print(str(block)[:14000].encode('ascii','backslashreplace').decode())

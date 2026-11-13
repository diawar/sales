import re
from urllib.parse import urlsplit

TYPES={'logo','heading','text','image','button','divider','spacer','product','footer'}

def validate_design(value):
    if value is None:return None
    if not isinstance(value,dict) or value.get('version')!=1:raise ValueError()
    brand=value.get('brand');blocks=value.get('blocks')
    if not isinstance(brand,dict) or not isinstance(blocks,list) or len(blocks)>100:raise ValueError()
    for key in ('accent','background','surface','text'):
        if not re.fullmatch(r'#[0-9a-fA-F]{6}',str(brand.get(key,''))):raise ValueError()
    if brand.get('font') not in ('Arial','Verdana','Georgia'):raise ValueError()
    if type(brand.get('width')) is not int or not 400<=brand['width']<=800:raise ValueError()
    for key in ('name','website','logo','contacts'):
        if not isinstance(brand.get(key,''),str) or len(brand.get(key,''))>2000:raise ValueError()
    for block in blocks:
        if not isinstance(block,dict) or block.get('type') not in TYPES:raise ValueError()
        for key,val in block.items():
            if not isinstance(val,str) or len(val)>10000:raise ValueError()
        if block.get('align','left') not in ('left','center','right'):raise ValueError()
    # Visual links accept explicit safe protocols only; imported HTML stays in HTML mode.
    for url in [brand.get('website',''),brand.get('logo','')]+[b.get(k,'') for b in blocks for k in ('url','image')]:
        if url and (urlsplit(url).scheme not in ('https','http','mailto','tel') or any(c in url for c in '\r\n')):raise ValueError()
    return value

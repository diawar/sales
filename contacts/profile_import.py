import hashlib
import uuid
from .profiles import validate_profile

ALIASES={'город':'city','city':'city','бин':'identifier','bin':'identifier','сайт':'websites','сайты':'websites','website':'websites','соцсети':'socials','socials':'socials','отрасль':'industry','вид деятельности':'industry','тип организации':'organization_type','тип учреждения':'organization_type','теги':'tags','контактное лицо':'person_name','должность':'person_role','email контакта':'person_email','телефон контакта':'person_phone'}

def parse_extra(item,root_name):
    identifier=item.get('identifier','').strip()
    if identifier and (len(identifier)!=12 or not identifier.isascii() or not identifier.isdigit()):raise ValueError('БИН должен содержать 12 цифр.')
    if identifier and root_name=='Физические лица':raise ValueError('Для физлиц поле БИН должно быть пустым.')
    values={k:item[k] for k in ['city','industry','organization_type'] if item.get(k)}
    for key in ['websites','tags']:
        if item.get(key):values[key]=[v.strip() for v in item[key].split('|') if v.strip()]
    if item.get('socials'):
        values['socials']=[]
        for entry in item['socials'].split('|'):
            service,sep,value=entry.partition('=')
            if not sep:raise ValueError('Соцсети: Сервис=ссылка или username, несколько через |.')
            values['socials'].append({'service':service.strip(),'value':value.strip()})
    if any(item.get(k) for k in ['person_name','person_role','person_email','person_phone']):
        values['contact_people']=[{k:item.get('person_'+k,'') for k in ['name','role','email','phone']}]
    return identifier,validate_profile(values)

def identity(name,email,identifier):
    if identifier:return 'kz:'+identifier
    if email:return hashlib.sha256((name.casefold()+'\0'+email).encode()).hexdigest()
    # An unidentified name-only record must not merge different people/companies.
    return 'import:'+uuid.uuid4().hex

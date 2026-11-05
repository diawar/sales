import json

FIELDS={'query','audience','activity','validation','source','delivered','clicks','archive','phone_kind','phone','city','profile_type','profile_tag','completeness','email_domain'}


def validate_rules(criteria, nested=False):
    if not isinstance(criteria,dict):raise ValueError('Некорректные условия сегмента.')
    cleaned={}
    for key,value in criteria.items():
        if key in {'$or','$exclude'} and not nested:
            if not isinstance(value,list) or not 1<=len(value)<=8:raise ValueError('Укажите от 1 до 8 групп условий.')
            cleaned[key]=[validate_rules(group,True) for group in value]
            if any(not group for group in cleaned[key]):raise ValueError('Группа условий не должна быть пустой.')
        elif key in FIELDS and (not nested or key!='archive') and isinstance(value,str) and len(value)<=300:
            if value!='':cleaned[key]=value
        else:raise ValueError('Некорректные условия сегмента.')
    return cleaned


def parse_rules(value):
    if not isinstance(value,str) or len(value)>20000:raise ValueError('Слишком большой набор условий.')
    try:return validate_rules(json.loads(value))
    except (ValueError,TypeError,RecursionError):raise ValueError('Некорректные условия сегмента.')

"""Domain routing checks: never claim that an individual mailbox exists."""
import dns.resolver
from django.core.exceptions import ValidationError
from django.core.validators import validate_email


def check_domain(domain, resolver=None):
    resolver=resolver or dns.resolver.Resolver()
    resolver.lifetime=5
    try:
        try:
            answer=list(resolver.resolve(domain,'MX'))
        except dns.resolver.NoAnswer:
            answer=[]
        if answer:
            hosts=[str(r.exchange).rstrip('.') for r in answer]
            if len(answer)==1 and answer[0].preference==0 and hosts==['']:
                return {'code':'null_mx','status':'Нет почтового маршрута','reason':'Домен явно не принимает почту (Null MX).'}
            if '' in hosts:
                return {'code':'temporary','status':'Временная ошибка','reason':'Противоречивые MX-записи.'}
            return {'code':'mx','status':'Домен принимает почту','reason':'Есть MX; существование ящика не проверено.','hosts':hosts}
        # RFC implicit MX: absence of MX alone is not a failure.
        for kind in ('A','AAAA'):
            try:
                if list(resolver.resolve(domain,kind)):
                    return {'code':'implicit_mx','status':'Домен принимает почту','reason':'Есть адрес домена при отсутствии MX; ящик не проверен.'}
            except dns.resolver.NoAnswer:
                pass
        return {'code':'no_route','status':'Нет почтового маршрута','reason':'Нет MX, A и AAAA.'}
    except dns.resolver.NXDOMAIN:
        return {'code':'nxdomain','status':'Домен не существует','reason':'DNS вернул NXDOMAIN.'}
    except dns.exception.DNSException:
        return {'code':'temporary','status':'Временная ошибка','reason':'DNS не дал достоверного ответа; повторите позже.'}


def check_email(email, cache=None, resolver=None):
    try:
        validate_email(email)
        domain=email.rsplit('@',1)[1].encode('idna').decode('ascii').lower()
    except (ValidationError,UnicodeError,IndexError):
        return {'code':'syntax','status':'Ошибка формата','reason':'Некорректный синтаксис email.'}
    cache=cache if cache is not None else {}
    if domain not in cache:cache[domain]=check_domain(domain,resolver)
    return {**cache[domain],'domain':domain}

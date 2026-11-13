"""Attribution and allowlisted audit snapshots. No recipients, HTML or credentials."""
import json
import re
import uuid
from contextvars import ContextVar
from django.core import signing
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.http import RawPostDataException
from .models import ActionAuditEntry, ActionAuditCampaign

COOKIE='sales_audit_operator'
SALT='sales-action-audit-v1'
context=ContextVar('action_audit_context',default=None)
ACTIONS={'created':'Создано','updated':'Изменено','prepared':'Очередь подготовлена','started':'Запущено',
    'approval_submitted':'На проверку','approval_approved':'Одобрено','approval_returned':'Возвращено','approval_invalidated':'Одобрение снято',
    'paused':'Приостановлено','resumed':'Продолжено','cancelled':'Отменено','auto_stopped':'Остановлено защитой',
    'completed':'Имитация завершена','settings_changed':'Настройки изменены','test_event':'Тестовое событие','attempted':'Попытка действия',
    'signed_in':'Вход','signed_out':'Выход','login_failed':'Вход отклонён','account_created':'Учётная запись создана',
    'account_changed':'Учётная запись изменена','password_changed':'Пароль изменён'}
TYPES={'campaign':'Рассылка','queue':'Очередь имитации','scenario':'Сценарий','simulation':'Имитация сценария',
    'rehearsal':'Репетиция','frequency':'Общая частота','operator':'Метка оператора','account':'Учётная запись'}
RESULTS={'success':'Успешно','rejected':'Отклонено','failed':'Ошибка запроса'}


def identity(request):
    try:
        value=json.loads(request.get_signed_cookie(COOKIE,salt=SALT,max_age=86400*365))
        identifier=str(uuid.UUID(value['id']));name=value.get('name','')
        if not isinstance(name,str) or len(name)>120 or any(ord(c)<32 for c in name):raise ValueError
        return {'id':identifier,'name':name}
    except (KeyError,ValueError,TypeError,signing.BadSignature):return {'id':str(uuid.uuid4()),'name':''}


def actor():
    ctx=context.get()
    if not ctx:return {'key':'system','label':'Система / служебный вызов','source':'system'}
    request=ctx['request'];user=getattr(request,'user',None)
    if user is not None and getattr(user,'is_authenticated',False):
        return {'key':('user:'+str(getattr(user,'pk',None) or user.get_username()))[:160],
            'label':str(user.get_username())[:180],'source':'authenticated'}
    remote=request.META.get('REMOTE_USER')
    if isinstance(remote,str) and remote.strip():
        return {'key':('remote:'+remote.strip())[:160],'label':remote.strip()[:180],'source':'remote_user'}
    who=ctx['identity']
    return {'key':'browser:'+who['id'],'label':who['name'] or 'Локальный оператор · '+who['id'][:8],
        'source':'declared' if who['name'] else 'anonymous'}


def safe_snapshot(value):
    # Callers supply explicit metadata only. Also discard sensitive keys defensively.
    forbidden={'password','secret','token','html','body','values','headers','subject','recipient','email','api_key'}
    def clean(item):
        if isinstance(item,dict):return {k:clean(v) for k,v in item.items() if isinstance(k,str) and not any(word in k.casefold() for word in forbidden)}
        if isinstance(item,(list,tuple)):return [clean(v) for v in item[:200]]
        return item
    return json.loads(json.dumps(clean(value or {}),cls=DjangoJSONEncoder))


@transaction.atomic
def record(action,kind,object_id,label='',*,campaigns=(),before=None,after=None,summary='',automatic=False,result='success'):
    who=actor();initiator={};ctx=context.get()
    if automatic:
        if who['source']!='system':initiator=who
        who={'key':'system','label':'Система (автоматическое действие)','source':'system'}
    entry=ActionAuditEntry.objects.create(actor_key=who['key'],actor_label=who['label'],actor_source=who['source'],
        initiated_by=initiator,action=action,object_type=kind,object_id=object_id or 0,object_label=str(label)[:180],
        before=safe_snapshot(before),after=safe_snapshot(after),summary=summary or ACTIONS[action],result=result,
        request_id=ctx['request_id'] if ctx else None)
    ActionAuditCampaign.objects.bulk_create([ActionAuditCampaign(entry=entry,campaign_id=cid) for cid in sorted(set(campaigns)) if type(cid) is int and cid>0])
    from .notifications import from_audit
    from_audit(entry)
    return entry


def campaign_snapshot(row):
    if not row or not row.pk:return {}
    return {k:getattr(row,k) for k in ('name','revision','template_id','sender_id','schedule_mode','scheduled_at','schedule_timezone')}|{'audience_ids':list(row.audiences.values_list('pk',flat=True))}


def scenario_snapshot(row):
    return {'name':row.name,'revision':row.revision,'config':row.config}


def scenario_campaigns(config):return [s['campaign_id'] for s in config.get('steps',[])]


def run_event(row,kind,detail,*,rehearsal=False,test_at=None,before=None):
    mapping={'started':'started','paused':'paused','resumed':'resumed','cancelled':'cancelled','safety_stopped':'auto_stopped'}
    if kind not in mapping:return
    record(mapping[kind],'rehearsal' if rehearsal else 'simulation',row.pk,row.snapshot.get('name',''),
        campaigns=scenario_campaigns(row.snapshot),summary=detail,automatic=kind=='safety_stopped',before=before,
        after={'state':row.state,'scenario_id':row.scenario_id,'scenario_revision':row.snapshot.get('revision'),
            'test_at':row.clock_at if rehearsal else test_at or row.started_at,
            **({'stop':row.snapshot.get('safety_stop')} if kind=='safety_stopped' else {})})


def rejected_target(request):
    """Only known action APIs. Never persist raw URLs, bearer links or request bodies."""
    if request.method!='POST':return None
    if not request.path.startswith(('/api/campaigns','/api/automations','/api/automation-simulation','/api/rehearsals','/api/test-frequency')):return None
    try:data=json.loads(request.body)
    except (ValueError,UnicodeDecodeError,RawPostDataException):data={}
    if not isinstance(data,dict):data={}
    operation={'pause':'paused','resume':'resumed','cancel':'cancelled','prepare':'prepared',
        'configure':'settings_changed','safety_configure':'settings_changed','event':'test_event','delivery_event':'test_event'}.get(data.get('action') if isinstance(data.get('action'),str) else '', 'attempted')
    routes=[(r'/api/campaigns(?:/(\d+))?','campaign','updated'),(r'/api/campaigns/(\d+)/queue','campaign',operation),
        (r'/api/campaigns/(\d+)/approval','campaign','attempted'),
        (r'/api/automations(?:/(\d+))?','scenario','updated'),(r'/api/automations/(\d+)/run','scenario','started'),
        (r'/api/rehearsals(?:/(\d+))?','rehearsal',operation),(r'/api/test-frequency','frequency','settings_changed')]
    for pattern,kind,action in routes:
        match=re.fullmatch(pattern,request.path)
        if match:
            oid=int(match.group(1) or 0) if match.re.groups else 1
            if oid>=2**63:oid=0
            if not oid and kind in ('campaign','scenario'):action='created'
            if not oid and kind=='rehearsal':action='started'
            return kind,oid,action
    if request.path=='/api/automation-simulation':
        value=data.get('run_id',0)
        return 'simulation',value if type(value) is int and 0<value<2**63 else 0,operation
    return None


class ActionAuditMiddleware:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        who=identity(request);ctx={'request':request,'identity':who,'request_id':uuid.uuid4()}
        marker=context.set(ctx)
        try:
            response=self.get_response(request)
            target=rejected_target(request)
            if target and response.status_code>=400:
                kind,oid,action=target
                # Failure attempts are separate from successful changes and contain no submitted content.
                record(action,kind,oid,result='rejected' if response.status_code<500 else 'failed',
                    campaigns=[oid] if kind=='campaign' and oid else [],
                    summary=f'Запрос действия: HTTP {response.status_code}. '+('Проверка параметров, версии или доступа не пройдена.' if response.status_code<500 else 'Ошибка ответа; проверьте успешные записи с тем же ID запроса.'))
            if request.path=='/' or request.path.startswith('/api/action-journal') or target:
                response.set_signed_cookie(COOKIE,json.dumps(ctx['identity']),salt=SALT,max_age=86400*365,
                    httponly=True,samesite='Strict',secure=request.is_secure())
            return response
        finally:context.reset(marker)

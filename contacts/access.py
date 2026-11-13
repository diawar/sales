"""Application roles. Unknown API routes are denied for non-administrators."""
import re
from .models import AccountRole

ROLES=dict(AccountRole._meta.get_field('role').choices)

def assigned_role(user):
    if not user.is_authenticated:return ''
    if user.is_superuser:return 'admin'
    try:return user.sales_role.role if user.sales_role.role in ROLES else 'viewer'
    except AccountRole.DoesNotExist:return 'viewer'

def role(user):
    return assigned_role(user) if user.is_authenticated and user.is_active else ''

def is_admin(user):return role(user)=='admin'

def permissions(user):
    value=role(user)
    return {'accounts':value=='admin','sources':value=='admin','connections':value=='admin',
        'templates':value in ('admin','manager','editor'),'operations':value in ('admin','manager'),
        'export':value in ('admin','manager'),'send':False}

READ_PATTERNS=[r'/api/contacts(?:/page|/summary|/\d+/detail)?',r'/api/audiences',r'/api/segments',
    r'/api/templates(?:/\d+)?',r'/api/campaigns(?:/\d+(?:/queue|/readiness|/mail-check)?)?',
    r'/api/campaign-senders',r'/api/campaign-stats',r'/api/subscriptions(?:/\d+)?',
    r'/api/activity-audiences',r'/api/mail-tracking',r'/api/automations(?:/\d+)?',
    r'/api/automation-simulation',r'/api/rehearsals(?:/\d+(?:/letters/\d+)?)?',r'/api/test-frequency',r'/api/action-journal']
WRITE_PATTERNS=[r'/api/contacts(?:/\d+/(?:notes|profile|email-status))?',r'/api/clients',r'/api/audiences',
    r'/api/import/(?:preview|commit)',r'/api/segments',r'/api/email-check',r'/api/campaigns(?:/preview|/recipients|/\d+(?:/queue|/readiness|/mail-check)?)?',
    r'/api/campaign-senders',r'/api/campaign-stats/(?:test-events|demo)',r'/api/subscriptions(?:/\d+(?:/unsubscribe-link)?)?',
    r'/api/activity-audiences/(?:demo|test-events)',r'/api/mail-tracking/preview',r'/api/automations(?:/\d+(?:/run)?)?',
    r'/api/automation-simulation',r'/api/rehearsals(?:/\d+(?:/letters/\d+)?)?',r'/api/test-frequency']

def allowed(user,path,method):
    value=role(user)
    if not value:return False
    # Sending is deliberately unavailable in this stage, including to administrators.
    if path=='/api/templates/test-mail':return method=='GET'
    if value=='admin':return True
    if path in ('/api/auth/session','/login/','/logout/','/setup/','/accounts/'):return True
    if not path.startswith('/api/'):return method in ('GET','HEAD','OPTIONS')
    if path=='/api/contacts/export':return value=='manager' and method=='GET'
    if path=='/api/notifications':return method in ('GET','POST','HEAD')
    if re.fullmatch(r'/api/campaigns/\d+/approval',path):return method in ('GET','HEAD') or value=='manager'
    if path=='/api/email-check':return value=='manager'
    if method in ('GET','HEAD'):return any(re.fullmatch(p,path) for p in READ_PATTERNS)
    if value in ('manager','editor') and re.fullmatch(r'/api/templates(?:/\d+|/check|/ai)?',path):return True
    return value=='manager' and any(re.fullmatch(p,path) for p in WRITE_PATTERNS)

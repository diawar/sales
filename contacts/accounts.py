"""Session accounts; credential handling stays in Django. No mail or external identity calls."""
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode
from urllib.parse import urlsplit
from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model, authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.forms import UserCreationForm, PasswordChangeForm, UsernameField
from django.contrib.sessions.models import Session
from django.core.validators import MaxLengthValidator
from django.db import transaction, connection
from django.db.models import F
from django.http import JsonResponse, HttpResponseRedirect
from django.shortcuts import render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET,require_POST,require_http_methods
from django.views.decorators.debug import sensitive_post_parameters
from .models import AccountAccessGate,AuthenticationLimit,AccountRole
from .access import role,assigned_role,is_admin,permissions,ROLES,allowed
from . import action_audit as audit
from .views import body,error

User=get_user_model()


def limit_passwords(fields,names):
    for key in names:
        fields[key].max_length=1024;fields[key].validators.append(MaxLengthValidator(1024))
        fields[key].widget.attrs['maxlength']=1024


class AccountForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        fields=('username','first_name')
        labels={'username':'Логин','first_name':'Имя (необязательно)'}
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.fields['password1'].label='Пароль';self.fields['password2'].label='Повторите пароль'
        self.fields['username'].widget.attrs['autocomplete']='username'
        limit_passwords(self.fields,('password1','password2'))
        for key in ('password1','password2'):
            self.fields[key].widget.attrs['autocomplete']='new-password'


class OwnPasswordForm(PasswordChangeForm):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);limit_passwords(self.fields,('old_password','new_password1','new_password2'))


class SetupForm(AccountForm):
    setup_code=forms.CharField(label='Одноразовый код настройки',max_length=200,widget=forms.PasswordInput(attrs={'autocomplete':'off'}))
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);self.order_fields(['setup_code','username','first_name','password1','password2'])


class SignInForm(forms.Form):
    username=UsernameField(label='Логин',max_length=150,widget=forms.TextInput(attrs={'autocomplete':'username','autofocus':True}))
    password=forms.CharField(label='Пароль',max_length=1024,strip=False,widget=forms.PasswordInput(attrs={'autocomplete':'current-password'}))


def lock_gate():
    if connection.vendor=='sqlite':exists=AccountAccessGate.objects.filter(pk=1).update(setup_digest=F('setup_digest'))
    else:exists=AccountAccessGate.objects.filter(pk=1).exists()
    if not exists:AccountAccessGate.objects.get_or_create(pk=1)
    return AccountAccessGate.objects.select_for_update().get(pk=1)


def private(response):
    # Native form POSTs need their same-origin source for Django's CSRF check.
    # Cross-origin destinations still receive no referrer.
    response['Cache-Control']='no-store, no-cache, max-age=0';response['Referrer-Policy']='same-origin'
    response['X-Frame-Options']='DENY';response['X-Robots-Tag']='noindex, nofollow'
    return response


@ensure_csrf_cookie
def csrf_failure(request,reason=''):
    lower=reason.casefold()
    category='origin' if 'origin' in lower or 'referer' in lower else 'missing_cookie' if 'cookie not set' in lower else 'missing_token' if 'token missing' in lower else 'token_mismatch'
    if request.path in ('/setup/','/login/','/logout/','/accounts/'):
        parsed=urlsplit(request.META.get('HTTP_ORIGIN',''))
        diagnostic={'csrf_reason':category,'origin_scheme':parsed.scheme,'origin_host':parsed.hostname or '',
            'request_host':request.get_host(),'secure_request':request.is_secure()}
        try:diagnostic['origin_port']=parsed.port
        except ValueError:diagnostic['origin_port']=None
        audit.record('login_failed' if request.path in ('/setup/','/login/') else 'attempted','account',0,'Проверка формы',
            result='rejected',after=diagnostic,summary='Проверка защитного кода формы не пройдена. Действие не выполнялось.')
    message='Защитный код формы устарел или не совпал с текущим сеансом. Откройте форму заново и повторите ввод.'
    if category=='missing_cookie':message='Браузер не передал cookie формы. Разрешите cookie для этого адреса, откройте форму заново и повторите ввод.'
    if request.path.startswith('/api/'):return private(JsonResponse({'error':message,'csrf_retry':True},status=403))
    retry='/setup/?fresh=1' if request.path=='/setup/' else '/login/' if request.path=='/login/' else '/accounts/' if request.user.is_authenticated else '/login/'
    return private(render(request,'accounts/csrf_error.html',{'message':message,'retry':retry},status=403))


def next_url(request):
    value=(request.POST if request.method=='POST' else request.GET).get('next','/')
    if not isinstance(value,str) or not value.startswith('/') or not url_has_allowed_host_and_scheme(value,{request.get_host()},require_https=request.is_secure()):return '/'
    return value


class AccessMiddleware:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        import re
        public=request.path in {'/login/','/setup/','/api/auth/session','/favicon.ico','/favicon.png','/favicon.svg','/auth.css','/subscription-form.js'}
        public=public or bool(re.fullmatch(r'/subscription/[A-Za-z0-9_-]+/|/track/o/[A-Za-z0-9_-]+\.gif|/track/c/[A-Za-z0-9_-]+/',request.path))
        if getattr(settings,'SALES_AUTH_REQUIRED',True) and not public and not request.user.is_authenticated:
            if request.path.startswith('/api/'):
                return private(JsonResponse({'error':'Требуется вход в учётную запись.','login_url':'/login/'},status=401))
            destination='/login/' if User.objects.exists() else '/setup/'
            return private(HttpResponseRedirect(destination+'?'+urlencode({'next':request.get_full_path()})))
        if getattr(settings,'SALES_AUTH_REQUIRED',True) and not public and request.user.is_authenticated and not allowed(request.user,request.path,request.method):
            audit.record('attempted','account',request.user.pk,'Проверка прав',result='rejected',
                after={'path':request.path,'method':request.method,'role':role(request.user)},summary='Доступ запрещён ролью пользователя.')
            return private(JsonResponse({'error':'Недостаточно прав для этого действия.','permission_denied':True},status=403))
        response=self.get_response(request)
        if getattr(settings,'SALES_AUTH_REQUIRED',True) and (not public or request.path in ('/login/','/setup/','/api/auth/session')):private(response)
        return response


def throttle_rows(request,name,prefix='login'):
    ip=request.META.get('REMOTE_ADDR','unknown')
    keys=[(prefix+':ip:'+ip,20),(prefix+':user:'+ip+':'+name.casefold(),5)]
    now=timezone.now();rows=[]
    for raw,maximum in keys:
        key=hashlib.sha256(raw.encode()).hexdigest()
        row,_=AuthenticationLimit.objects.get_or_create(key=key,defaults={'window_at':now})
        if row.window_at+timedelta(minutes=15)<=now:
            row.window_at=now;row.failures=0;row.save()
        if row.failures>=maximum:return None
        rows.append(row)
    return rows


def failed(rows):
    for row in rows:row.failures+=1;row.save(update_fields=['failures'])


def account_data(user):
    return {'id':user.pk,'username':user.username,'name':user.first_name,'active':user.is_active,
        'administrator':assigned_role(user)=='admin','role':assigned_role(user),'role_label':ROLES.get(assigned_role(user),'Доступ отключён'),
        'permissions':permissions(user),'last_login':user.last_login,'created_at':user.date_joined}


@require_GET
@ensure_csrf_cookie
def session(request):
    user=request.user
    return private(JsonResponse({'authenticated':user.is_authenticated,'user':account_data(user) if user.is_authenticated else None,
        'setup_required':not User.objects.exists(),'can_send':False}))


@require_GET
def account_list(request):
    if not request.user.is_authenticated:return private(error('Требуется вход.',401))
    if not is_admin(request.user):return private(error('Управление учётными записями доступно администратору.',403))
    return private(JsonResponse({'users':[account_data(u) for u in User.objects.order_by('username')],'can_send':False}))


@sensitive_post_parameters('password','password1','password2','setup_code')
@require_http_methods(['GET','POST'])
@ensure_csrf_cookie
def sign_in(request):
    if request.user.is_authenticated:return private(HttpResponseRedirect(next_url(request)))
    if not User.objects.exists():return private(HttpResponseRedirect('/setup/'))
    form=SignInForm(request.POST or None);message='';status=200
    if request.method=='POST':
        with transaction.atomic():
            lock_gate();name=str(request.POST.get('username',''))[:150]
            rows=throttle_rows(request,name)
            if rows is None:message='Слишком много попыток. Повторите вход через 15 минут.';status=429
            else:
                user=None
                if form.is_valid():
                    candidates=User.objects.filter(username__iexact=form.cleaned_data['username'])
                    actual=candidates.first() if candidates.count()==1 else None
                    user=authenticate(request,username=actual.username if actual else form.cleaned_data['username'],password=form.cleaned_data['password'])
                if user:
                    login(request,user);rows[-1].failures=0;rows[-1].save(update_fields=['failures'])
                    audit.record('signed_in','account',user.pk,user.username,summary='Вход по учётной записи выполнен.')
                    return private(HttpResponseRedirect(next_url(request)))
                failed(rows);message='Не удалось войти. Проверьте логин и пароль.';status=400
            audit.record('login_failed','account',0,'Вход',result='rejected',summary='Попытка входа отклонена.' if status!=429 else 'Попытка входа ограничена по частоте.')
    response=render(request,'accounts/login.html',{'form':form,'message':message,'next':next_url(request)},status=status)
    if status==429:response['Retry-After']='900'
    return private(response)


@sensitive_post_parameters('password1','password2','setup_code')
@require_http_methods(['GET','POST'])
@ensure_csrf_cookie
def setup(request):
    if User.objects.exists():return private(HttpResponseRedirect('/login/'))
    form=SetupForm(request.POST or None);message='';status=200
    if request.method=='POST':
        with transaction.atomic():
            gate=lock_gate()
            if User.objects.exists():return private(HttpResponseRedirect('/login/'))
            rows=throttle_rows(request,'initial','setup')
            valid=gate.setup_digest and gate.setup_expires_at and gate.setup_expires_at>timezone.now()
            raw=request.POST.get('setup_code','')
            valid=valid and isinstance(raw,str) and secrets.compare_digest(hashlib.sha256(raw.encode()).hexdigest(),gate.setup_digest)
            if rows is None:message='Слишком много попыток. Повторите через 15 минут.';status=429
            elif not valid:
                failed(rows);message='Одноразовый код недействителен или срок его действия истёк.';status=400
            elif form.is_valid():
                user=form.save(commit=False);user.is_staff=True;user.is_superuser=True;user.save()
                gate.setup_digest='';gate.setup_expires_at=None;gate.save()
                login(request,user)
                audit.record('account_created','account',user.pk,user.username,after={'username':user.username,'active':True,'administrator':True},summary='Первая учётная запись создана по одноразовому коду.')
                audit.record('signed_in','account',user.pk,user.username,summary='Первый вход выполнен.')
                return private(HttpResponseRedirect('/'))
            else:failed(rows);status=400
    gate=AccountAccessGate.objects.filter(pk=1).first()
    ready=bool(gate and gate.setup_digest and gate.setup_expires_at and gate.setup_expires_at>timezone.now())
    return private(render(request,'accounts/setup.html',{'form':form,'message':message,'ready':ready},status=status))


@require_POST
def sign_out(request):
    if request.user.is_authenticated:
        audit.record('signed_out','account',request.user.pk,request.user.username,summary='Выход выполнен; текущая сессия завершена.')
    logout(request)
    return private(HttpResponseRedirect('/login/'))


def accounts_page(request,create,change,message,status=200):
    users=list(User.objects.select_related('sales_role').order_by('username')) if is_admin(request.user) else []
    for person in users:
        person.access_role=assigned_role(person);person.access_role_label=ROLES.get(assigned_role(person),'Доступ отключён')
    response=private(render(request,'accounts/accounts.html',{'create_form':create,'password_form':change,'users':users,
        'message':message,'administrator':is_admin(request.user),'roles':ROLES.items()},status=status))
    if status==429:response['Retry-After']='900'
    return response


@sensitive_post_parameters('password1','password2','old_password','new_password1','new_password2')
@require_http_methods(['GET','POST'])
@ensure_csrf_cookie
def accounts(request):
    if not request.user.is_authenticated:return private(HttpResponseRedirect('/login/'))
    create=AccountForm();change=OwnPasswordForm(request.user);message='';status=200
    if request.method=='POST':
        action=request.POST.get('action')
        with transaction.atomic():
            lock_gate()
            if action=='create':
                if not is_admin(User.objects.get(pk=request.user.pk)):
                    audit.record('account_created','account',0,'Управление учётными записями',result='rejected',summary='Недостаточно прав для создания пользователя.')
                    return error('Управление учётными записями доступно администратору.',403)
                create=AccountForm(request.POST)
                if create.is_valid():
                    user=create.save()
                    AccountRole.objects.create(user=user,role='viewer')
                    audit.record('account_created','account',user.pk,user.username,after={'username':user.username,'active':user.is_active,'administrator':False},summary='Учётная запись создана администратором.')
                    create=AccountForm();message='Учётная запись создана.'
                else:status=400
            elif action=='active':
                if not is_admin(User.objects.get(pk=request.user.pk)):
                    audit.record('account_changed','account',0,'Управление учётными записями',result='rejected',summary='Недостаточно прав для изменения доступа.')
                    return error('Управление учётными записями доступно администратору.',403)
                try:target=User.objects.select_for_update().get(pk=int(request.POST.get('user_id','')))
                except (ValueError,User.DoesNotExist):return error('Учётная запись не найдена.',404)
                if request.POST.get('active') not in ('0','1'):return error('Проверьте новый статус доступа.',400)
                active=request.POST.get('active')=='1'
                if not active and (target.pk==request.user.pk or is_admin(target) and not any(is_admin(u) for u in User.objects.filter(is_active=True).exclude(pk=target.pk))):
                    message='Нельзя отключить текущего или последнего администратора.';status=400
                elif target.is_active!=active:
                    before=target.is_active;target.is_active=active;target.save(update_fields=['is_active'])
                    if not active:
                        for session_row in Session.objects.filter(expire_date__gte=timezone.now()).iterator():
                            if session_row.get_decoded().get('_auth_user_id')==str(target.pk):session_row.delete()
                    audit.record('account_changed','account',target.pk,target.username,before={'active':before},after={'active':active},summary='Статус доступа учётной записи изменён.')
                    message='Статус учётной записи изменён.'
            elif action=='role':
                if not is_admin(User.objects.get(pk=request.user.pk)):return error('Недостаточно прав.',403)
                try:target=User.objects.select_for_update().get(pk=int(request.POST.get('user_id','')))
                except (ValueError,User.DoesNotExist):return error('Учётная запись не найдена.',404)
                new_role=request.POST.get('role','')
                if new_role not in ROLES:return error('Неизвестная роль.',400)
                if target.pk==request.user.pk:return error('Нельзя менять собственную роль.',400)
                if target.is_superuser:return error('Роль первого администратора защищена.',400)
                before=assigned_role(target)
                AccountRole.objects.update_or_create(user=target,defaults={'role':new_role})
                audit.record('account_changed','account',target.pk,target.username,before={'role':before},after={'role':new_role},summary='Роль пользователя изменена администратором. Новые права действуют со следующего запроса.')
                message='Роль изменена. Права действуют со следующего запроса.'
            elif action=='password':
                limits=throttle_rows(request,request.user.username,'password')
                if limits is None:
                    return accounts_page(request,create,change,'Слишком много попыток. Повторите через 15 минут.',429)
                change=OwnPasswordForm(request.user,request.POST)
                if change.is_valid():
                    user=change.save();update_session_auth_hash(request,user)
                    audit.record('password_changed','account',user.pk,user.username,summary='Пользователь изменил свой пароль. Другие сессии будут завершены.')
                    change=OwnPasswordForm(request.user);message='Пароль изменён.'
                else:
                    failed(limits);status=400
                    audit.record('password_changed','account',request.user.pk,request.user.username,result='rejected',summary='Изменение пароля не прошло проверку.')
            else:return error('Неизвестное действие.',400)
    return accounts_page(request,create,change,message,status)

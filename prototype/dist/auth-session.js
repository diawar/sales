'use strict';
(()=>{
async function refresh(){try{const response=await fetch('/api/auth/session',{cache:'no-store'}),data=await response.json();if(!data.authenticated){location.replace(data.setup_required?'/setup/':'/login/?next='+encodeURIComponent(location.pathname+location.search));return;}const host=document.querySelector('.account');if(!host)return;host.replaceChildren();const avatar=document.createElement('span');avatar.className='avatar';avatar.textContent=(data.user.name||data.user.username).slice(0,1).toUpperCase();const identity=document.createElement('span'),name=document.createElement('b'),note=document.createElement('small');name.textContent=data.user.name||data.user.username;note.textContent=data.user.role_label||'Пользователь';identity.append(name,note);const link=document.createElement('a');link.href='/accounts/';link.setAttribute('aria-label','Учётная запись: '+data.user.username);link.title=data.user.username;const label=document.createElement('span');label.className='account-link-label';label.textContent='Учётная запись';const mobileName=document.createElement('span');mobileName.className='account-link-user';mobileName.textContent=data.user.name||data.user.username;link.append(label,mobileName);const form=document.createElement('form');form.action='/logout/';form.method='post';const token=document.createElement('input');token.type='hidden';token.name='csrfmiddlewaretoken';token.value=document.cookie.split('; ').find(v=>v.startsWith('sales_csrftoken='))?.split('=')[1]||'';const button=document.createElement('button');button.type='submit';button.className='outline';button.textContent='Выйти';form.append(token,button);host.append(avatar,identity,link,form);applyRights(data.user);}catch{}}
let rights;
function applyRights(user){rights=user.permissions;document.documentElement.dataset.accessRole=user.role;
let notice=document.getElementById('access-role-notice');if(!notice){notice=document.createElement('p');notice.id='access-role-notice';notice.style.cssText='padding:10px 16px;margin:0;background:#edf6fc;color:#193047';document.querySelector('main')?.prepend(notice);}notice.textContent='Ваша роль: '+user.role_label+'. '+(rights.operations?'Реальная отправка отключена.':rights.templates?'Изменение доступно только для шаблонов.':'Просмотр без изменений и экспорта.');
restrict();}
function restrict(){if(!rights)return;for(const b of document.querySelectorAll('button')){const t=b.textContent.trim();let denied=false;
if(/^(Отправить тест|Отправить письмо|Тестовая отправка)/.test(t))denied=true;
if(!rights.sources&&/^(Сбор данных|Журнал|Настройки)$/.test(t))denied=true;
if(!rights.operations&&/^(Добавить клиента|Импорт CSV|↓ Экспорт|Добавить подкатегорию|Запустить проверку|Пауза|Подтвердить импорт|Создать рассылку|Создать сценарий|Начать|Запустить|Продолжить|Отменить|Подготовить очередь|Добавить событие|Создать подписку)/.test(t))denied=true;
if(!rights.templates&&/^(Сохранить|Создать шаблон|Новый шаблон|Удалить|Сохранить копию)/.test(t))denied=true;
if(denied){b.disabled=true;b.title='Недоступно для вашей роли';}}
}
new MutationObserver(()=>restrict()).observe(document.body,{childList:true,subtree:true});
refresh();window.addEventListener('pageshow',event=>{if(event.persisted)refresh();});
})();

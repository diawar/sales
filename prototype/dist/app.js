'use strict';
const $=id=>document.getElementById(id);
const data=window.CONTACTS||[];
let page=1,sortKey='name',sortDirection=1,selected=new Set();
let pagination=window.INITIAL_PAGE.pagination;
let pendingRequest=0,filterTimer=null,loading=false;
let segmentGroups={};
const filters=['query','audience','activity','validation','source','delivered','clicks','archive','phone_kind','phone','city','profile_type','profile_tag','completeness','email_domain'];
const kindName={mobile:'Сотовый',landline:'Городской',service:'Сервисный',unknown:'Не определён'};
function filtered(){return data;}
function badge(text,cls){const el=document.createElement('span');el.className=`badge ${cls}`;el.textContent=text;return el;}
function queryParams(){
  const params=new URLSearchParams({page:String(page),page_size:$('page-size').value,sort:sortKey,direction:sortDirection===1?'asc':'desc'});
  for(const key of filters){const el=$(key);if(!el)continue;const value=key==='archive'?(el.checked?'1':'0'):el.value;if(value)params.set(key,value);}
  if(Object.keys(segmentGroups).length)params.set('segment_rules',JSON.stringify(segmentGroups));
  return params;
}
function prepareRows(rows){for(const r of rows){r.phone=(r.phones||[]).map(p=>p.number).join(' | ');r.profile_type=r.industry||r.organization_type||'';r.profile_tag=(r.tags||[]).map(t=>'#'+t).join(' ');}}
function selectionText(){$('selection').textContent=selected.size?`Выбрано контактов: ${selected.size}`:`Уникальных адресов в выборке: ${pagination.unique_emails}`;}
function render(){
  prepareRows(data);$('rows').replaceChildren();
  for(const r of data){
    const tr=document.createElement('tr');tr.dataset.contactId=r.id;tr.classList.toggle('selected',selected.has(r.id));
    const cell=document.createElement('td'),check=document.createElement('input');check.type='checkbox';check.checked=selected.has(r.id);check.disabled=loading;check.setAttribute('aria-label',`Выбрать ${r.name}`);
    check.onchange=()=>{check.checked?selected.add(r.id):selected.delete(r.id);render();};cell.append(check);tr.append(cell);
    for(const k of ['name','email','audience','activity','validation','delivered','opens','clicks','source']){
      const td=document.createElement('td');td.className=['delivered','opens','clicks'].includes(k)?'number':k;
      if(k==='activity')td.append(badge(r[k],r[k]==='Исключены'?'red':''));
      else if(k==='validation')td.append(badge(r[k],r[k]==='Вероятно рабочий'?'green':r[k]==='Временная ошибка'?'orange':r[k]==='Несуществующий'?'red':''));
      else td.textContent=k==='audience'?r.audiences.join(', '):r[k];
      if(k==='name'){const small=document.createElement('small');small.textContent=`Контакт ${r.id}${r.archived?' · Архив':''}`;td.append(small);}tr.append(td);
      if(k==='validation'&&r.validation_checked_at){const small=document.createElement('small');small.textContent='Проверено: '+new Date(r.validation_checked_at).toLocaleString('ru-RU');td.append(small);td.title=r.validation_result?.reason||'';}
    }
    if($('phone_kind')){
      const td=document.createElement('td');for(const phone of r.phones||[]){const div=document.createElement('div'),small=document.createElement('small');div.textContent=phone.number;small.textContent=kindName[phone.kind]||kindName.unknown;div.append(small);td.append(div);}if(!td.childNodes.length)td.textContent='—';tr.append(td);
    }
    if($('city'))for(const key of ['city','profile_type','profile_tag']){const td=document.createElement('td');if(key==='profile_tag'&&r.tags?.length)tagChips(td,r.tags);else td.textContent=r[key]||'—';tr.append(td);}
    $('rows').append(tr);
  }
  const total=pagination.total,size=pagination.page_size;
  $('count').textContent=total;$('range').textContent=total?`${(pagination.page-1)*size+1}–${Math.min(pagination.page*size,total)} из ${total} контактов`:'0 контактов';
  $('page-label').textContent=`${pagination.page} / ${pagination.pages}`;
  $('prev').disabled=loading||!pagination.has_previous;$('next').disabled=loading||!pagination.has_next;$('empty').hidden=total>0;
  const checked=data.filter(r=>selected.has(r.id)).length;
  $('select-all').checked=data.length>0&&checked===data.length;$('select-all').indeterminate=checked>0&&checked<data.length;$('select-all').disabled=loading;
  document.querySelectorAll('[data-sort]').forEach(b=>{const active=b.dataset.sort===sortKey;b.classList.toggle('sorted',active);b.parentElement.setAttribute('aria-sort',active?(sortDirection===1?'ascending':'descending'):'none');b.textContent=b.textContent.replace(/[↕↑↓]/g,'').trim()+' '+(active?(sortDirection===1?'↑':'↓'):'↕');});selectionText();
}
async function loadContactPage(){
  clearTimeout(filterTimer);const request=++pendingRequest;loading=true;$('rows').setAttribute('aria-busy','true');$('notice').textContent='Загрузка контактов…';render();
  try{
    const payload=await api('/api/contacts/page?'+queryParams());
    if(request!==pendingRequest)return;
    data.splice(0,data.length,...payload.contacts);pagination=payload.pagination;page=pagination.page;$('notice').textContent='';
  }catch(error){if(request!==pendingRequest)return;$('notice').textContent=error.message;}
  finally{if(request===pendingRequest){loading=false;$('rows').removeAttribute('aria-busy');render();}}
}
function filtersChanged(){
  page=1;selected.clear();clearTimeout(filterTimer);
  pendingRequest++;loading=true;render();filterTimer=setTimeout(loadContactPage,250);
}
function addSortHeader(key,title){const th=document.createElement('th'),button=document.createElement('button');button.dataset.sort=key;button.textContent=title+' ↕';th.append(button);document.querySelector('thead tr').append(th);}
document.querySelector('.filter-grid').addEventListener('input',e=>{if(filters.includes(e.target.id))filtersChanged();});
$('page-size').onchange=()=>{page=1;loadContactPage();};
$('reset').onclick=()=>{segmentGroups={};renderSegmentGroups();for(const id of filters){const el=$(id);if(el)id==='archive'?el.checked=false:el.value='';}page=1;selected.clear();loadContactPage();};
document.querySelector('thead').onclick=e=>{const b=e.target.closest('[data-sort]');if(!b)return;sortDirection=sortKey===b.dataset.sort?-sortDirection:1;sortKey=b.dataset.sort;page=1;loadContactPage();};
$('prev').onclick=()=>{if(loading)return;page--;loadContactPage();};$('next').onclick=()=>{if(loading)return;page++;loadContactPage();};
$('select-all').onchange=e=>{data.forEach(r=>e.target.checked?selected.add(r.id):selected.delete(r.id));render();};
$('export').onclick=async()=>{
  const button=$('export');button.disabled=true;$('notice').textContent='Подготовка CSV всей выборки…';
  const params=queryParams();params.delete('page');params.delete('page_size');if(selected.size)params.set('ids',[...selected].join(','));
  try{
    const response=await fetch('/api/contacts/export?'+params);
    if(!response.ok){const value=await response.json();throw Error(value.error||'Не удалось экспортировать контакты.');}
    const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='contacts.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    $('notice').textContent=selected.size?'CSV выбранных контактов готов.':'CSV всей выборки готов.';
  }catch(error){$('notice').textContent=error.message;}finally{button.disabled=false;}
};
const stats=window.CONTACT_SUMMARY.stats;
const emailCheckPanel=document.createElement('section');emailCheckPanel.className='panel';
emailCheckPanel.innerHTML='<div class="section-title"><h2>Проверка email</h2></div><p>Синтаксис и почтовые DNS-настройки. Наличие ящика не подтверждается. Контакты не удаляются.</p><div class="actions"><button class="outline" data-email-action="start">Запустить проверку</button><button class="outline" data-email-action="pause">Пауза</button><button class="outline" data-email-action="refresh">Обновить</button></div><p role="status" id="email-check-status"></p>';
document.querySelector('.filters').before(emailCheckPanel);
let emailCheckBusy=false;
async function refreshEmailCheck(action){
  if(emailCheckBusy)return;emailCheckBusy=true;
  const buttons=emailCheckPanel.querySelectorAll('button');buttons.forEach(b=>b.disabled=true);
  try{
    const s=await api('/api/email-check',action&&action!=='refresh'?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})}:{});
    $('email-check-status').textContent=`${s.enabled?'Проверка включена':'На паузе'} · Проверено: ${s.checked} из ${s.total} · В очереди сейчас: ${s.due} · Ожидают повтора: ${s.retry}. ${s.worker_alive?'Фоновый процесс работает.':'Фоновый процесс не запущен.'} ${s.error}`;
  }catch(error){$('email-check-status').textContent=error.message;}
  finally{emailCheckBusy=false;buttons.forEach(b=>b.disabled=false);}
}
emailCheckPanel.onclick=e=>{const b=e.target.closest('[data-email-action]');if(b)refreshEmailCheck(b.dataset.emailAction);};
refreshEmailCheck();setInterval(()=>refreshEmailCheck(),20000);
for(const status of ['Домен принимает почту','Домен не существует','Нет почтового маршрута','Ошибка формата'])$('validation').add(new Option(status,status));
const completenessLabel=document.createElement('label');completenessLabel.textContent='Заполненность клиента';
const completenessSelect=document.createElement('select');completenessSelect.id='completeness';
for(const [value,title] of [['','Любая заполненность'],['missing_both','Без email и телефона'],['with_email','Есть email'],['with_phone','Есть телефон']])completenessSelect.add(new Option(title,value));
completenessLabel.append(completenessSelect);document.querySelector('.filter-grid').append(completenessLabel);
const domainLabel=document.createElement('label');domainLabel.textContent='Домен email';
const domainInput=document.createElement('input');domainInput.id='email_domain';domainInput.placeholder='Например, mail.ru';domainInput.autocomplete='off';
domainLabel.append(domainInput);document.querySelector('.filter-grid').append(domainLabel);
const coverage=window.CONTACT_SUMMARY.coverage;
if(coverage){
  const section=document.createElement('section');section.className='stats';section.setAttribute('aria-label','Заполненность клиентов');
  for(const [key,title] of [['clients','Всего клиентов'],['with_email','С email'],['with_phone','С телефоном'],['missing_both','Без email и телефона']]){
    const article=document.createElement('article'),label=document.createElement('span'),value=document.createElement('strong'),note=document.createElement('small');
    label.textContent=title;value.textContent=coverage[key];note.textContent='Уникальные клиенты · вся база';article.append(label,value,note);section.append(article);
  }
  document.querySelector('.stats').after(section);
}
$('total').textContent=stats.working;$('active').textContent=stats.active;$('passive').textContent=stats.passive;$('attention').textContent=stats.attention;$('nav-count').textContent=stats.total;
render();
const segmentsPanel=document.createElement('section');segmentsPanel.className='panel';
segmentsPanel.innerHTML='<div class="section-title"><h2>Сохранённые сегменты</h2></div><p>Динамические выборки: при открытии состав пересчитывается. Условия объединяются через И.</p><label>Сегмент<select id="saved-segment"><option value="">Выберите сегмент</option></select></label><div class="actions"><button class="outline" data-segment-action="apply">Применить</button><button class="outline" data-segment-action="preview">Проверить состав</button><button class="outline" data-segment-action="create">Сохранить фильтры как новый</button><button class="outline" data-segment-action="update">Обновить выбранный</button></div><p id="segment-status" role="status"></p>';
document.querySelector('.filters').before(segmentsPanel);
let savedSegments=[];
async function refreshSegments(id=''){
  const result=await api('/api/segments');savedSegments=result.segments;
  $('saved-segment').replaceChildren(new Option('Выберите сегмент',''));
  for(const segment of savedSegments)$('saved-segment').add(new Option(segment.name,String(segment.id)));
  $('saved-segment').value=String(id);
}
function currentCriteria(){const params=queryParams();return Object.fromEntries([...params].filter(([key])=>filters.includes(key)));}
function fullCriteria(){return {...currentCriteria(),...segmentGroups};}
const groupTools=document.createElement('div');
groupTools.innerHTML='<p>Общие фильтры ниже действуют через И. Добавьте из них варианты ИЛИ или исключения, затем задайте общие ограничения. Исключение убирает клиента целиком.</p><div class="actions"><button class="outline" data-segment-action="or">Добавить текущие фильтры как ИЛИ</button><button class="outline" data-segment-action="exclude">Исключить по текущим фильтрам</button><button class="outline" data-segment-action="draft">Проверить текущие условия</button></div><div id="segment-groups"></div>';
segmentsPanel.append(groupTools);
segmentsPanel.querySelector('p').textContent='Динамические выборки: общие фильтры И (любая группа ИЛИ), за вычетом исключений. Состав пересчитывается.';
function renderSegmentGroups(){
  const container=$('segment-groups');if(!container)return;container.replaceChildren();
  for(const key of ['$or','$exclude'])for(const [index,group] of (segmentGroups[key]||[]).entries()){
    const p=document.createElement('p'),remove=document.createElement('button');
    p.textContent=(key==='$or'?'ИЛИ: ':'ИСКЛЮЧИТЬ: ')+Object.entries(group).map(([field,value])=>{
      const input=$(field),label=input?.closest('label')?.firstChild?.textContent||field;
      const option=input?.tagName==='SELECT'?[...input.options].find(o=>o.value===value)?.textContent:null;
      return `${label.trim()}: ${option||value}`;
    }).join(' И ');
    remove.className='outline';remove.textContent='Убрать группу';remove.onclick=()=>{segmentGroups[key].splice(index,1);if(!segmentGroups[key].length)delete segmentGroups[key];renderSegmentGroups();filtersChanged();};p.append(' ',remove);container.append(p);
  }
}
async function segmentRequest(payload){return api('/api/segments',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});}
segmentsPanel.onclick=async e=>{
  const button=e.target.closest('[data-segment-action]');if(!button)return;
  const action=button.dataset.segmentAction,segment=savedSegments.find(s=>String(s.id)===$('saved-segment').value);
  try{
    if(action==='or'||action==='exclude'){
      const group=currentCriteria();delete group.archive;
      if(!Object.keys(group).length)throw Error('Сначала задайте хотя бы один фильтр.');
      const key=action==='or'?'$or':'$exclude';if((segmentGroups[key]||[]).length>=8)throw Error('Допустимо до 8 групп каждого вида.');
      (segmentGroups[key]??=[]).push(group);
      for(const id of filters){const el=$(id);if(el&&id!=='archive')el.value='';}
      renderSegmentGroups();filtersChanged();$('segment-status').textContent='Группа добавлена. Можно задать следующий вариант или общие фильтры.';return;
    }
    if(action==='draft'){
      const result=await segmentRequest({action:'preview',filters:fullCriteria()});
      $('segment-status').textContent=`Текущие условия: клиентов ${result.counts.clients}, строк контактов ${result.counts.contacts}, уникальных email ${result.counts.emails}.`;return;
    }
    if(action==='apply'){
      if(!segment)throw Error('Выберите сегмент.');
      for(const id of filters){const el=$(id);if(el)id==='archive'?el.checked=segment.filters[id]==='1':el.value=segment.filters[id]||'';}
      segmentGroups=JSON.parse(JSON.stringify(Object.fromEntries(Object.entries(segment.filters).filter(([key])=>key.startsWith('$')))));renderSegmentGroups();
      page=1;selected.clear();await loadContactPage();$('segment-status').textContent='Применён сегмент: '+segment.name;return;
    }
    if(action==='preview'){
      if(!segment)throw Error('Выберите сегмент.');
      const result=await segmentRequest({action:'preview',id:segment.id});
      $('segment-status').textContent=`${segment.name}: клиентов ${result.counts.clients}, строк контактов ${result.counts.contacts}, уникальных email ${result.counts.emails}. Это наличие адресов, а не разрешение на отправку.`;return;
    }
    if(action==='update'&&!segment)throw Error('Выберите сегмент для обновления.');
    const criteria=fullCriteria(),dialog=document.createElement('dialog');dialog.className='client-dialog';
    dialog.innerHTML='<h2>Сохранить сегмент</h2><form><label>Название<input name="name" maxlength="120" required></label><p>Сохраняются текущие фильтры, без выбранных строк и номера страницы. Состав будет пересчитываться.</p><div class="actions"><button class="outline">Сохранить</button><button type="button" class="outline">Отмена</button></div><p role="status"></p></form>';
    dialog.querySelector('input').value=action==='update'?segment.name:'';document.body.append(dialog);dialog.showModal();
    dialog.querySelector('[type=button]').onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();
    dialog.querySelector('form').onsubmit=async event=>{
      event.preventDefault();const submit=dialog.querySelector('button');submit.disabled=true;
      try{const result=await segmentRequest({name:dialog.querySelector('input').value,filters:criteria,...(action==='update'?{id:segment.id}:{})});await refreshSegments(result.id);$('segment-status').textContent=`Сохранено: ${result.name}. Клиентов: ${result.counts.clients}; уникальных email: ${result.counts.emails}.`;dialog.close();}
      catch(error){dialog.querySelector('[role=status]').textContent=error.message;}finally{submit.disabled=false;}
    };
  }catch(error){$('segment-status').textContent=error.message;}
};
refreshSegments().catch(error=>{$('segment-status').textContent=error.message;});

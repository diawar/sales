'use strict';
const profileLabels={name:'Имя / название',city:'Город',industry:'Отрасль / вид деятельности',organization_type:'Тип учреждения / организации',websites:'Сайты',socials:'Соцсети и мессенджеры',contact_people:'Контактные лица',tags:'Теги',region_kato:'Регион источника',gov_contact:'Контакты госоргана',phone_purpose:'Назначение телефона',dfo_address:'Адрес ДФО',dfo_region:'Регион ДФО',dfo_type:'Тип ДФО',dfo_status:'Статус ДФО',dfo_contacts:'Контакты ДФО',production_address:'Адрес производства',oked_codes:'ОКЭД источника',oked_names:'Деятельность по источнику'};
function installProfileUI(){
  const add=document.createElement('button');add.className='outline';add.textContent='Добавить клиента';document.querySelector('.heading').append(add);add.onclick=()=>editProfile(null);
  let opening=0;
  dialog.addEventListener('close',()=>{opening++;});
  openClient=async r=>{
    const request=++opening;
    focusedContact=r;
    document.getElementById('client-title').textContent=r.name;
    const content=document.getElementById('client-content');
    const message=document.createElement('p');message.textContent='Загрузка карточки…';content.replaceChildren(message);
    if(!dialog.open)dialog.showModal();
    try{
      const detail=await api(`/api/contacts/${r.id}/detail`);
      if(request!==opening||!dialog.open)return;
      editProfile({...r,...detail});
    }catch(error){
      if(request!==opening||!dialog.open)return;
      message.textContent=error.message;
      const retry=document.createElement('button');retry.className='outline';retry.textContent='Повторить';retry.onclick=()=>openClient(r);content.append(retry);
    }
  };
  const pending=sessionStorage.getItem('open-contact');if(pending){sessionStorage.removeItem('open-contact');openClient(data.find(r=>r.id===Number(pending))||{id:Number(pending),name:'Клиент'});}
  const grid=document.querySelector('.filter-grid');const fields={city:'Город',profile_type:'Отрасль / тип организации',profile_tag:'Тег'};
  for(const [key,title]of Object.entries(fields)){const label=document.createElement('label');label.textContent=title;const input=document.createElement('input');input.id=key;input.placeholder='Все';label.append(input);grid.append(label);}
  for(const [key,title]of [['city','Город'],['profile_type','Отрасль / тип'],['profile_tag','Теги']])addSortHeader(key,title);
  render();
}
function editProfile(r){
  const create=!r;focusedContact=r;const content=document.getElementById('client-content');content.replaceChildren();document.getElementById('client-title').textContent=create?'Новый клиент':r.name;
  const tabs=document.createElement('div');tabs.className='actions profile-tabs';const basic=document.createElement('button');basic.className='outline';basic.textContent='Карточка';const historyButton=document.createElement('button');historyButton.className='outline';historyButton.textContent='Источники и история';tabs.append(basic,historyButton);content.append(tabs);historyButton.disabled=create;
  const form=document.createElement('form');form.className='profile-form';const history=document.createElement('section');history.hidden=true;content.append(form,history);basic.onclick=()=>{form.hidden=false;history.hidden=true;};historyButton.onclick=()=>{form.hidden=true;history.hidden=false;};
  const controls={};function input(key,title,value='',multiline=false){const label=document.createElement('label');label.textContent=title;const control=document.createElement(multiline?'textarea':'input');control.value=value;control.name=key;if(multiline)control.rows=3;label.append(control);controls[key]=control;form.append(label);return label;}
  let audienceControl=null;if(create){const label=document.createElement('label');label.textContent='Аудитория';audienceControl=document.createElement('select');for(const a of window.AUDIENCES)audienceControl.add(new Option(a.label,a.id));label.append(audienceControl);form.append(label);}else{const p=document.createElement('p');p.className='muted';p.textContent=(r.audiences||[]).join(' · ');form.append(p);}
  input('name','Имя / название',r?.name||'');controls.name.required=true;controls.name.maxLength=300;
  const binLabel=input('identifier','БИН / идентификатор организации',r?.identifier||'');controls.identifier.maxLength=12;controls.identifier.readOnly=!create;
  input('city','Город',r?.city||'');controls.city.maxLength=160;
  const industryLabel=input('industry','Отрасль / вид деятельности',r?.industry||'');
  const typeLabel=input('organization_type','Тип учреждения / организации',r?.organization_type||'');
  input('emails','Email — по одному в строке',(r?.all_emails||[]).join('\n'),true);
  input('phones','Телефоны — по одному в строке; тип определяется автоматически',[...new Set((r?.phones||[]).map(p=>p.number))].join('\n'),true);
  const sitesLabel=input('websites','Сайты — по одному в строке',(r?.websites||[]).join('\n'),true);controls.websites.placeholder='https://example.com';
  input('socials','Соцсети и мессенджеры — Сервис=ссылка или username',(r?.socials||[]).map(s=>s.service+'='+s.value).join('\n'),true);controls.socials.placeholder='Telegram=@username\nInstagram=https://instagram.com/example';
  const peopleLabel=input('contact_people','Контактные лица — Имя | Должность | Email | Телефон',(r?.contact_people||[]).map(p=>[p.name,p.role,p.email,p.phone].join(' | ')).join('\n'),true);
  input('tags','Теги',(r?.tags||[]).join(' '));const tagEditor=createTagEditor(controls.tags);input('notes','Заметки',r?.notes||'',true);controls.notes.maxLength=4000;
  function category(){const ids=create?[Number(audienceControl.value)]:(r.audience_ids||[]);const roots=window.AUDIENCES.filter(a=>ids.includes(a.id)).map(a=>(a.parent_id?window.AUDIENCES.find(p=>p.id===a.parent_id):a)?.name);const person=roots.length>0&&roots.every(n=>n==='Физические лица');const government=roots.includes('Государственные структуры');binLabel.hidden=person;industryLabel.hidden=person||government;typeLabel.hidden=person;sitesLabel.hidden=person;peopleLabel.hidden=person;controls.name.parentElement.firstChild.textContent=person?'Имя / ФИО':'Название организации';}
  if(audienceControl)audienceControl.onchange=category;category();const info=document.createElement('p');info.className='muted';info.textContent='Новые email и телефоны добавляются к существующим. История и запреты сохраняются. Тип телефона определяется по телефонному плану; мессенджер не проверяется по номеру.';form.append(info);const status=document.createElement('p');status.setAttribute('role','status');const save=document.createElement('button');save.className='outline';save.textContent='Сохранить клиента';form.append(save,status);
  const lines=value=>value.split('\n').map(v=>v.trim()).filter(Boolean);
  form.onsubmit=async event=>{event.preventDefault();save.disabled=true;status.textContent='Сохранение…';try{const payload={name:controls.name.value,city:controls.city.value,notes:controls.notes.value,emails:lines(controls.emails.value),phones:lines(controls.phones.value),tags:tagEditor.read(),socials:lines(controls.socials.value).map(line=>{const at=line.indexOf('=');if(at<1)throw Error('Соцсети: укажите Сервис=ссылка или username.');return{service:line.slice(0,at).trim(),value:line.slice(at+1).trim()};})};if(!industryLabel.hidden)payload.industry=controls.industry.value;if(!typeLabel.hidden)payload.organization_type=controls.organization_type.value;if(!sitesLabel.hidden)payload.websites=lines(controls.websites.value);if(!peopleLabel.hidden)payload.contact_people=lines(controls.contact_people.value).map(line=>{const p=line.split('|').map(v=>v.trim());if(p.length>4)throw Error('Контактное лицо: не более четырёх полей через |.');return{name:p[0]||'',role:p[1]||'',email:p[2]||'',phone:p[3]||''};});if(create){payload.audience_id=Number(audienceControl.value);payload.identifier=binLabel.hidden?'':controls.identifier.value;}const value=await api(create?'/api/clients':`/api/contacts/${r.id}/profile`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});sessionStorage.setItem('open-contact',String(create?value.contact_id:r.id));location.reload();}catch(error){status.textContent=error.message;save.disabled=false;}};
  if(r){const sh=document.createElement('h3');sh.textContent='Источники email и карточки';history.append(sh);for(const entry of (r.contact_sources?r.contact_sources.flatMap(c=>c.evidence.map(e=>({...e,email:c.email}))):r.evidence||[])){const p=document.createElement('p');p.textContent=(entry.email?entry.email+' · ':'')+entry.source+' · '+(entry.current?'Опубликован':'Исторический')+' ';if(entry.url){const a=document.createElement('a');a.href=entry.url;a.textContent='Источник';a.target='_blank';a.rel='noopener noreferrer';p.append(a);}history.append(p);}const ph=document.createElement('h3');ph.textContent='Телефоны и источники';history.append(ph);for(const phone of r.phones||[]){const p=document.createElement('p');p.textContent=phone.number+' · '+({mobile:'Сотовый',landline:'Городской',unknown:'Не определён'}[phone.kind]||'Не определён')+' · '+phone.source;history.append(p);}const hh=document.createElement('h3');hh.textContent='Значения полей и изменения — последние 100 записей';history.append(hh);const states={accepted:'Выбрано',alternative:'Альтернативное значение',superseded:'История',evidence:'Данные источника'};for(const entry of r.field_history||[]){const row=document.createElement('div');row.className='timeline-item';const b=document.createElement('b');b.textContent=(profileLabels[entry.field]||entry.field)+' · '+(states[entry.state]||entry.state);const value=document.createElement('p');value.textContent=typeof entry.value==='string'?entry.value:JSON.stringify(entry.value);const source=document.createElement('small');source.textContent=entry.source+' · '+new Date(entry.last_seen).toLocaleString('ru-RU');row.append(b,value,source);if(entry.state==='alternative'&&['name','city','industry','organization_type'].includes(entry.field)){const accept=document.createElement('button');accept.className='outline';accept.textContent='Выбрать это значение';accept.onclick=async()=>{accept.disabled=true;try{await api(`/api/contacts/${r.id}/profile`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({[entry.field]:entry.value})});sessionStorage.setItem('open-contact',String(r.id));location.reload();}catch(error){accept.textContent=error.message;accept.disabled=false;}};row.append(accept);}history.append(row);}if(!r.field_history?.length){const p=document.createElement('p');p.textContent='Изменения дополнительных полей ещё не записаны.';history.append(p);}}
  if(!dialog.open)dialog.showModal();
}



'use strict';
window.CampaignQueue={open(campaignId){return new Promise(resolve=>{
 const dialog=document.createElement('dialog');dialog.className='client-dialog';dialog.style.maxWidth='900px';dialog.style.width='min(900px,94vw)';
 const heading=document.createElement('div');heading.className='dialog-heading';const title=document.createElement('h2');title.textContent='Очередь — имитация';
 const close=document.createElement('button');close.className='outline';close.textContent='Закрыть';close.onclick=()=>dialog.close();heading.append(title,close);
 const note=document.createElement('p');note.textContent='Письма не отправляются. Результат «Имитировано» не означает доставку. Ошибки имитации не блокируют реальные email.';
 const content=document.createElement('section'),status=document.createElement('p');status.setAttribute('role','status');dialog.append(heading,note,content,status);document.body.append(dialog);dialog.showModal();
 let timer,token=0;dialog.onclose=()=>{clearTimeout(timer);token++;dialog.remove();resolve();};
 const url=`/api/campaigns/${campaignId}/queue`,labels={paused:'На паузе',running:'Обработка',completed:'Завершена',cancelled:'Отменена'},results={pending:'Ожидают',retry:'Ожидают повтора',simulated:'Имитировано',failed:'Ошибки имитации',skipped:'Исключены при проверке',cancelled:'Отменены'};
 function text(tag,value){const e=document.createElement(tag);e.textContent=value;return e;}
 function button(label,action){const b=text('button',label);b.className='outline';b.type='button';b.onclick=()=>act({action});return b;}
 function date(value,zone){return value?new Date(value).toLocaleString('ru-RU',{timeZone:zone||'Asia/Qyzylorda'}):'—';}
 async function act(data){++token;clearTimeout(timer);dialog.querySelectorAll('button').forEach(b=>b.disabled=true);dialog.inert=true;status.textContent='Сохранение…';try{const payload=await api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});status.textContent='';render(payload);}catch(e){status.textContent=e.message;}finally{dialog.inert=false;dialog.querySelectorAll('button').forEach(b=>b.disabled=false);}}
 async function refresh(){const request=++token;clearTimeout(timer);try{const data=await api(url);if(request===token&&dialog.open){status.textContent='';render(data);}}catch(e){if(request===token)status.textContent=e.message;}}
 function render(data){content.replaceChildren();const row=data.queue;
  content.append(text('p',data.worker_alive?'Обработчик имитации работает.':'Обработчик имитации не запущен. Состояние очереди сохранено в БД.'));
  const refreshButton=button('Обновить','');refreshButton.onclick=refresh;content.append(refreshButton);
  if(row){
   content.append(text('h3',labels[row.state]||row.state),text('p',`Получателей в зафиксированной очереди: ${row.total}. Версия черновика: ${row.campaign_revision}.`));
   if(row.approval_stop)content.append(text('p','Остановка согласования: '+row.approval_stop));
   content.append(text('p',`Не более ${row.rate_per_minute} попыток/мин на эту рассылку, включая повторы. Максимум попыток: ${row.max_attempts}. Задержка повтора: ${row.retry_seconds} сек с увеличением ×2.`));
   content.append(text('p',`Запуск не раньше: ${date(row.ready_at,row.timezone)} (${row.timezone}). Следующий слот: ${date(row.next_slot_at,row.timezone)}.`));
   const counters=document.createElement('div');counters.className='cmp-count-grid';for(const [key,label]of Object.entries(results)){const box=text('div',label);box.append(text('strong',String(row.stats[key]||0)));counters.append(box);}content.append(counters);
   const actions=document.createElement('div');actions.className='actions';if(row.state==='running')actions.append(button('Пауза','pause'));if(row.state==='paused')actions.append(button('Продолжить имитацию','resume'));if(['running','paused'].includes(row.state))actions.append(button('Отменить очередь','cancel'));content.append(actions);
   content.append(text('p','Состав очереди зафиксирован. Новые контакты не добавляются; новые запреты учитываются перед каждой попыткой. После отмены необработанные записи сохраняются в истории.'));
  }
  if(!row||row.state==='paused'){
   const form=document.createElement('form');form.style.marginTop='16px';const fields={};
   for(const [name,label,value,min,max]of [['rate_per_minute','Лимит попыток в минуту',row?.rate_per_minute||30,1,600],['max_attempts','Максимум попыток на получателя',row?.max_attempts||3,1,10],['retry_seconds','Начальная задержка повтора, сек',row?.retry_seconds||60,1,3600]]){const l=text('label',label),input=document.createElement('input');input.type='number';input.min=min;input.max=max;input.step=1;input.required=true;input.value=value;input.style.width='100%';input.style.display='block';input.setAttribute('aria-label',label);fields[name]=input;l.append(input);form.append(l);}
   const label=text('label','Сценарий имитации'),scenario=document.createElement('select');scenario.setAttribute('aria-label','Сценарий имитации');scenario.style.width='100%';scenario.style.display='block';for(const [v,t]of Object.entries(data.scenarios))scenario.add(new Option(t,v));scenario.value=row?.scenario||'success';label.append(scenario);form.append(label);
   const save=text('button',row?'Сохранить настройки':'Подготовить очередь');save.className='primary';save.type='submit';form.append(save);form.onsubmit=async e=>{e.preventDefault();const values=Object.fromEntries(Object.entries(fields).map(([key,input])=>[key,Number(input.value)]));let revision;if(!row){try{revision=(await api('/api/campaigns/'+campaignId)).revision;}catch(e){status.textContent=e.message;return;}}act({...values,scenario:scenario.value,mode:'simulation',action:row?'configure':'prepare',campaign_revision:revision});};content.append(form);
  }
  if(row?.history.length){content.append(text('h3','Последние 20 попыток имитации'));const scroll=document.createElement('div');scroll.className='table-scroll';const table=document.createElement('table'),head=document.createElement('tr');for(const label of ['Email','Попытка','Результат','Время'])head.append(text('th',label));table.append(head);for(const item of row.history){const tr=document.createElement('tr');for(const value of [item.email,String(item.number),{simulated:'Имитировано',temporary:'Временная ошибка',permanent:'Окончательная ошибка'}[item.outcome]||item.outcome,date(item.created_at,row.timezone)])tr.append(text('td',value));table.append(tr);}scroll.append(table);content.append(scroll);}
  if(row?.state==='running')timer=setTimeout(refresh,2000);
 }
 refresh();
});}};

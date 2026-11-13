'use strict';
(() => {
  const label=document.createElement('label');label.textContent='Допуск к отправке';
  const select=document.createElement('select');select.id='send_status';
  for(const [v,t] of [['','Все состояния'],['ready','DNS проверен'],['excluded','Исключены / ошибка email'],['pending','Нужна проверка / без email']])select.add(new Option(t,v));
  label.append(select);document.querySelector('.filter-grid').append(label);
  const button=document.createElement('button');button.className='outline';button.textContent='Проверка email';document.querySelector('.heading').append(button);
  button.onclick=()=>showChecks();
  const previous=openClient;
  openClient=async row=>{await previous(row);if(!row.email)return;const b=document.createElement('button');b.className='outline';b.textContent='Проверка email и исключения';b.onclick=()=>showChecks(row.id);document.getElementById('client-content').append(b);};
  function element(tag,text){const e=document.createElement(tag);e.textContent=text;return e;}
  async function showChecks(id){
    const d=document.createElement('dialog');d.className='client-dialog';
    const close=element('button','Закрыть');close.className='outline';close.onclick=()=>d.close();
    d.append(element('h2',id?'Email и исключения':'Проверка email'),close);
    const status=element('p','Загрузка…');status.setAttribute('role','status');const content=document.createElement('section');d.append(status,content);document.body.append(d);d.showModal();
    let timer;d.onclose=()=>{clearTimeout(timer);d.remove();};
    async function refresh(){
      if(!d.open)return;
      try{
        const queue=await api('/api/email-check');content.replaceChildren();
        content.append(element('p',`Проверено: ${queue.checked} из ${queue.total}. Ожидают: ${queue.due}. Повтор: ${queue.retry}.`));
        content.append(element('p',queue.worker_alive?(queue.enabled?'Очередь запущена.':'Очередь на паузе.'):'Обработчик не запущен. Очередь сохраняется в БД.'));
        if(queue.error)content.append(element('p',queue.error));
        const toggle=element('button',queue.enabled?'Приостановить проверку':'Запустить проверку');toggle.className='outline';toggle.onclick=()=>act('/api/email-check',{action:queue.enabled?'pause':'start'});content.append(toggle);
        content.append(element('p','Проверяются формат и DNS. Наличие почтового маршрута не подтверждает существование ящика, доставку или согласие. Клиенты не удаляются. Повторный импорт не снимает запреты.'));
        if(id){
          const info=await api(`/api/contacts/${id}/email-status`);
          content.append(element('h3',info.email),element('p',`${info.sending.label}: ${info.sending.reason}`),element('p',`Проверка: ${info.validation}${info.checked_at?' · '+new Date(info.checked_at).toLocaleString('ru-RU'):''}`));
          const recheck=element('button','Поставить на повторную проверку');recheck.className='outline';recheck.onclick=()=>act(`/api/contacts/${id}/email-status`,{action:'recheck'});content.append(recheck);
          const form=document.createElement('form');const reason=document.createElement('select');reason.setAttribute('aria-label','Причина исключения');
          for(const [v,t] of [['manual','Ручной запрет'],['unsubscribe','Отписка'],['complaint','Жалоба'],['hard_bounce','Окончательная ошибка доставки']])reason.add(new Option(t,v));
          const note=document.createElement('input');note.maxLength=500;note.placeholder='Комментарий / основание';note.setAttribute('aria-label','Комментарий');
          const add=element('button','Исключить из отправки');add.className='outline';form.append(reason,note,add);form.onsubmit=e=>{e.preventDefault();act(`/api/contacts/${id}/email-status`,{action:'exclude',reason:reason.value,note:note.value});};content.append(form);
          content.append(element('p','Запрет действует на этот email во всех аудиториях. Для снятия укажите основание; после отписки необходимо новое согласие.'));
          for(const entry of info.history){
            const item=document.createElement('div');item.className='panel';item.append(element('p',`${entry.label} · ${new Date(entry.created_at).toLocaleString('ru-RU')} · ${entry.note||'Без комментария'}`));
            if(entry.removed_at)item.append(element('p',`Снят: ${new Date(entry.removed_at).toLocaleString('ru-RU')} · ${entry.removal_note}`));
            else{const restore=element('button','Снять этот запрет');restore.className='outline';restore.onclick=()=>act(`/api/contacts/${id}/email-status`,{action:'restore',reason:entry.reason,note:note.value});item.append(restore);}content.append(item);
          }
        }
        status.textContent='';
        // Avoid rerendering the form while a comment is being entered.
        if(!id)timer=setTimeout(refresh,5000);
      }catch(e){status.textContent=e.message;}
    }
    async function act(url,data){
      const buttons=[...content.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
      try{await api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});clearTimeout(timer);await refresh();await loadContactPage();}
      catch(e){status.textContent=e.message;buttons.forEach(b=>b.disabled=false);}
    }
    await refresh();
  }
})();

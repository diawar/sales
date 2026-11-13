'use strict';
(()=>{
 const form=document.querySelector('main form');if(!form)return;
 const status=document.createElement('p');status.setAttribute('role','status');form.after(status);
 form.addEventListener('submit',async event=>{
  event.preventDefault();const button=form.querySelector('button');button.disabled=true;status.textContent='Сохранение…';
  try{
   const fields=new URLSearchParams(new FormData(form));
   const cookie=document.cookie.split('; ').find(v=>v.startsWith('sales_csrftoken='))?.split('=')[1];
   const csrf=cookie||fields.get('csrfmiddlewaretoken');fields.set('csrfmiddlewaretoken',csrf||'');
   const response=await fetch(location.href,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json','X-CSRFToken':csrf||''},body:fields.toString()});
   let data;try{data=await response.json();}catch{throw Error('Не удалось проверить запрос. Обновите страницу и повторите действие.');}
   if(!response.ok)throw Error(data.error||'Действие не выполнено.');
   document.querySelector('h1').textContent='Готово';document.title='Готово — подписка';
   document.getElementById('subscription-message').textContent=data.message;
   form.remove();status.textContent='';
  }catch(e){status.textContent=e.message;}finally{if(form.isConnected)button.disabled=false;}
 });
})();

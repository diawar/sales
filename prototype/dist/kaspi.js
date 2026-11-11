'use strict';
const kaspiPanel=document.createElement('section');kaspiPanel.className='panel source-card';
kaspiPanel.innerHTML=`<div class="section-title"><h2>Kaspi — компьютеры и комплектующие</h2><span class="badge">Автоматический сбор · Алматы</span></div>
<p>Бизнес → Kaspi. Продавцы найдены через предложения товаров в Алматы. Город адреса магазина учитывается отдельно от выбранного города покупателя. Один продавец может иметь несколько категорий.</p>
<div style="display:flex;flex-wrap:wrap;gap:12px;margin:12px 0"><button class="primary" id="kaspi-start">Включить сбор</button><button class="outline" id="kaspi-pause">Пауза</button><button class="outline" id="kaspi-retry">Повторить отложенные</button></div>
<p id="kaspi-worker" role="status"></p><p id="kaspi-summary"></p>
<div style="display:flex;flex-wrap:wrap;gap:12px;margin:12px 0"><label>Категория Kaspi <select id="kaspi-category"><option value="">Все категории</option><option>Процессоры</option><option>Системные блоки</option></select></label><label>Поиск магазина <input id="kaspi-search" placeholder="Название, ID, город"></label><button class="outline" id="kaspi-refresh">Обновить таблицу</button></div>
<div class="table-scroll" id="kaspi-table"></div>
<div style="display:flex;gap:12px;align-items:center;margin:12px 0"><button class="outline" id="kaspi-prev">Назад</button><span id="kaspi-page"></span><button class="outline" id="kaspi-next">Далее</button></div>
<p>Не чаще одного публичного запроса в 10 секунд. Прогресс сохраняется; после обхода следующий цикл через 24 часа. Товарные цены и заказы не собираются. Публикация контактов не означает согласие на рассылку.</p>`;
collectionPage.append(kaspiPanel);
let kaspiRequest=0,kaspiPage=1;
async function refreshKaspi(){
  const current=++kaspiRequest;
  try{
    const params=new URLSearchParams({category:document.getElementById('kaspi-category').value,q:document.getElementById('kaspi-search').value,page:kaspiPage});
    const r=await api('/api/kaspi?'+params);if(current!==kaspiRequest)return;
    const c=r.counts||{},next=r.next_request_at?new Date(r.next_request_at).toLocaleString('ru-RU'):'';
    document.getElementById('kaspi-worker').textContent=`${r.enabled?'Сбор включён':'Сбор на паузе'}. ${r.worker_alive?'Фоновый обработчик доступен.':'Обработчик не отвечает: запустите manage.py collect_kaspi.'} Цикл ${r.cycle}. Обработано задач: ${c.done||0}, в очереди: ${c.queued||0}, на проверке: ${(c.review||0)+(c.blocked||0)}. ${next?'Следующий запрос/цикл: '+next+'. ':''}${r.error||''}`;
    document.getElementById('kaspi-summary').textContent=`Сохранено магазинов: ${r.result.merchants}. Уникальных телефонов: ${r.result.phones}. Email: ${r.result.emails}. В выборке: ${r.total}.`;
    const categorySelect=document.getElementById('kaspi-category'),selected=categorySelect.value;
    categorySelect.replaceChildren(new Option('Все категории',''),...r.categories.map(name=>new Option(name,name)));categorySelect.value=selected;
    const table=document.createElement('table'),head=document.createElement('thead'),tr=document.createElement('tr');
    for(const label of ['ID Kaspi','Магазин','Категории','Город адреса','Телефон','Адрес','Источник']){const th=document.createElement('th');th.textContent=label;tr.append(th);}head.append(tr);table.append(head);
    const body=document.createElement('tbody');for(const row of r.rows){const tr=document.createElement('tr');for(const value of [row.merchant_id,row.shop_name,row.categories.join(', '),row.city||'Не указан',row.phones.map(p=>p.number+' ('+({mobile:'сотовый',landline:'городской',service:'сервисный',unknown:'не определён'}[p.kind])+')').join('; '),row.addresses.join('; ')]){const td=document.createElement('td');td.textContent=value;tr.append(td);}const td=document.createElement('td'),link=document.createElement('a');link.textContent='Карточка Kaspi';link.href=row.source_url;link.target='_blank';link.rel='noopener noreferrer';td.append(link);tr.append(td);body.append(tr);}table.append(body);document.getElementById('kaspi-table').replaceChildren(table);
    kaspiPage=r.page;document.getElementById('kaspi-page').textContent=`Страница ${r.page} из ${r.pages}`;
    document.getElementById('kaspi-prev').disabled=r.page<=1;document.getElementById('kaspi-next').disabled=r.page>=r.pages;
  }catch(error){if(current===kaspiRequest)document.getElementById('kaspi-summary').textContent=error.message;}
}
document.getElementById('kaspi-refresh').onclick=refreshKaspi;
document.getElementById('kaspi-category').onchange=()=>{kaspiPage=1;refreshKaspi();};
document.getElementById('kaspi-search').onkeydown=e=>{if(e.key==='Enter'){kaspiPage=1;refreshKaspi();}};
for(const action of ['start','pause','retry'])document.getElementById('kaspi-'+action).onclick=async()=>{
  const button=document.getElementById('kaspi-'+action);button.disabled=true;
  try{await api('/api/kaspi',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});await refreshKaspi();}
  catch(error){document.getElementById('kaspi-worker').textContent=error.message;}finally{button.disabled=false;}
};
document.getElementById('kaspi-prev').onclick=()=>{kaspiPage--;refreshKaspi();};
document.getElementById('kaspi-next').onclick=()=>{kaspiPage++;refreshKaspi();};
refreshKaspi();
setInterval(()=>{if(!collectionPage.hidden)refreshKaspi();},5000);

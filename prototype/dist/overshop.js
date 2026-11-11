'use strict';
const overShopPanel=document.createElement('section');overShopPanel.className='panel source-card';
overShopPanel.innerHTML=`<div class="section-title"><h2>Over-shop.kz</h2><span class="badge">Клиенты и подписки сайта</span></div>
<p class="source-caption">Физические лица → Over-shop.kz</p>
<p>Загрузка из сохранённой выгрузки сайта. Администраторы исключены; признаки организаций и служебных записей вынесены на проверку. Постоянная синхронизация не подключена.</p>
<p id="overshop-summary"></p>
<div class="filters" style="display:flex;flex-wrap:wrap;gap:12px;margin:16px 0">
<label>Категория <select id="overshop-category"><option value="person_candidate">Кандидаты в физические лица</option><option value="staff">Администраторы — исключены</option><option value="organization_review">Организации — на проверку</option><option value="service_review">Служебные записи — на проверку</option><option value="">Все записи</option></select></label>
<label>Покупки <select id="overshop-purchase"><option value="">Все</option><option value="paid">Есть оплаченные покупки</option><option value="no_orders">Не делали заказов</option><option value="unpaid">Есть заказы без полной оплаты</option><option value="cancelled">Только отменённые заказы</option></select></label>
<label>Сортировка <select id="overshop-sort"><option value="id_desc">ID: сначала новые</option><option value="id_asc">ID: сначала старые</option><option value="orders_desc">Заказы: по убыванию</option><option value="orders_asc">Заказы: по возрастанию</option><option value="name">Имя</option><option value="email">Email</option></select></label>
<label>Поиск <input id="overshop-search" placeholder="ID, имя, email, телефон, компания"></label>
<button class="outline" id="overshop-refresh">Обновить таблицу</button></div>
<p>Оплата и отмена указаны по данным сайта на момент выгрузки. Неоплаченный заказ не означает отсутствие заказов. Активность в рассылках и согласие на рекламу учитываются отдельно.</p>
<p id="overshop-status" role="status"></p><div class="table-scroll" id="overshop-preview"></div>
<div style="display:flex;align-items:center;gap:12px;margin-top:12px"><button class="outline" id="overshop-prev">Назад</button><span id="overshop-page"></span><button class="outline" id="overshop-next">Далее</button></div>`;
collectionPage.append(overShopPanel);
let overShopPage=1,overShopRequest=0;
async function refreshOverShop(){
  const requestId=++overShopRequest,button=document.getElementById('overshop-refresh');button.disabled=true;
  try{
    const params=new URLSearchParams({page:overShopPage,category:document.getElementById('overshop-category').value,purchase:document.getElementById('overshop-purchase').value,sort:document.getElementById('overshop-sort').value,q:document.getElementById('overshop-search').value});
    const r=await api('/api/overshop/preview?'+params);if(requestId!==overShopRequest)return;
    const container=document.getElementById('overshop-preview');container.replaceChildren();
    if(!r.available){document.getElementById('overshop-status').textContent='Выгрузка ещё не подготовлена.';return;}
    if(!r.full_review){document.getElementById('overshop-status').textContent='Полная проверка ещё не подготовлена. Доступен первоначальный образец.';return;}
    const s=r.summary,c=s.categories,p=s.candidate_purchases;
    document.getElementById('overshop-summary').textContent=`Всего ${s.records} пользователей с уникальными ID. Кандидатов: ${c.person_candidate||0}; администраторов: ${c.staff||0}; организаций на проверку: ${c.organization_review||0}; служебных записей на проверку: ${c.service_review||0}. Среди кандидатов: оплаченные покупки — ${p.paid||0}, без заказов — ${p.no_orders||0}, без полной оплаты — ${p.unpaid||0}, только отменённые — ${p.cancelled||0}.`;
    document.getElementById('overshop-status').textContent=`Найдено ${r.total}. ${r.full_imported?`Клиенты загружены: ${r.import_result.created+r.import_result.updated}. Подписки перенесены: ${r.import_result.subscriptions}.`:(r.pilot?.verified?`Пробный импорт: ${r.pilot.source_ids.length} клиентов. Полная загрузка не выполнена.`:'Импорт не выполнен.')} Категория «кандидат» не подтверждает тип физического лица.`;
    const table=document.createElement('table'),head=document.createElement('thead'),tr=document.createElement('tr');
    for(const label of ['ID сайта','Имя','Email','Телефон','Город','Категория','Покупки','Заказов / оплачено / отменено','Причина / замечания']){const th=document.createElement('th');th.textContent=label;tr.append(th);}head.append(tr);table.append(head);
    const body=document.createElement('tbody');for(const row of r.preview){const tr=document.createElement('tr');for(const value of [row.source_customer_id,row.name,row.email,row.phone,row.city,r.categories[row.category],row.purchase_label,`${row.orders_total} / ${row.orders_paid} / ${row.orders_cancelled}`,[...row.reasons,...row.issues].join('; ')]){const td=document.createElement('td');td.textContent=value||'Не указан';tr.append(td);}body.append(tr);}table.append(body);container.append(table);
    overShopPage=r.page;document.getElementById('overshop-page').textContent=`Страница ${r.page} из ${r.pages}`;
    document.getElementById('overshop-prev').disabled=r.page<=1;document.getElementById('overshop-next').disabled=r.page>=r.pages;
  }catch(error){if(requestId===overShopRequest)document.getElementById('overshop-status').textContent=error.message;}finally{if(requestId===overShopRequest)button.disabled=false;}
}
document.getElementById('overshop-refresh').onclick=()=>{overShopPage=1;refreshOverShop();};
for(const key of ['category','purchase','sort'])document.getElementById('overshop-'+key).onchange=()=>{overShopPage=1;refreshOverShop();};
document.getElementById('overshop-search').onkeydown=event=>{if(event.key==='Enter'){overShopPage=1;refreshOverShop();}};
document.getElementById('overshop-prev').onclick=()=>{overShopPage--;refreshOverShop();};
document.getElementById('overshop-next').onclick=()=>{overShopPage++;refreshOverShop();};
refreshOverShop();

const overSubscriptions=document.createElement('section');overSubscriptions.className='panel source-card';
overSubscriptions.innerHTML=`<h2>Подписчики Over-shop.kz</h2>
<p>Статусы из классического модуля «Сервисы → Рассылки → Подписчики». Регистрация и покупки не означают подписку. Анонимная подписка не подтверждает владельца аккаунта. Локальные отписки и запреты имеют приоритет.</p>
<p id="overshop-sub-summary" role="status"></p>
<div style="display:flex;flex-wrap:wrap;gap:12px;margin:12px 0">
<label>Статус подписки <select id="overshop-sub-state"><option value="">Все</option><option value="confirmed">Подтверждена</option><option value="pending">Ожидает подтверждения</option><option value="inactive">Неактивна</option><option value="suppressed">Исключена из отправки</option><option value="review">На проверку</option></select></label>
<label>Email подписчика <input id="overshop-sub-query" type="search"></label>
<button class="outline" id="overshop-sub-refresh">Найти подписчиков</button></div>
<div class="table-scroll" id="overshop-sub-table"></div>
<div style="display:flex;gap:12px;align-items:center;margin-top:12px"><button class="outline" id="overshop-sub-prev">Назад</button><span id="overshop-sub-page"></span><button class="outline" id="overshop-sub-next">Далее</button></div>`;
collectionPage.append(overSubscriptions);
let overSubPage=1,overSubRequest=0;
async function refreshOverSubscriptions(){
  const current=++overSubRequest;
  try{
    const query=new URLSearchParams({state:document.getElementById('overshop-sub-state').value,q:document.getElementById('overshop-sub-query').value,page:overSubPage});
    const r=await api('/api/overshop/subscribers?'+query);if(current!==overSubRequest)return;
    const labels={confirmed:'Подтверждена',pending:'Ожидает подтверждения',inactive:'Неактивна',suppressed:'Исключена из отправки',review:'На проверку'};
    document.getElementById('overshop-sub-summary').textContent=`Аккаунтов сайта в БД: ${r.accounts_imported}. Найдено подписок: ${r.total}. ${Object.entries(r.states).map(([key,value])=>labels[key]+': '+value).join('; ')}. Рассылка не подключена.`;
    const table=document.createElement('table'),head=document.createElement('thead'),tr=document.createElement('tr');
    for(const label of ['ID подписки','ID аккаунта','Email','Подтверждена на сайте','Активна на сайте','Рубрики','Дата подписки','Дата подтверждения','Текущий статус']){const th=document.createElement('th');th.textContent=label;tr.append(th);}head.append(tr);table.append(head);
    const body=document.createElement('tbody');for(const row of r.rows){const tr=document.createElement('tr');for(const value of [row.subscriber_id,row.source_customer_id||'Анонимная',row.email,row.confirmed?'Да':'Нет',row.active?'Да':'Нет',row.rubrics.join(', '),row.created_at,row.confirmed_at,labels[row.state]+(row.review_reason?' — '+row.review_reason:'')]){const td=document.createElement('td');td.textContent=value||'Не указана';tr.append(td);}body.append(tr);}table.append(body);document.getElementById('overshop-sub-table').replaceChildren(table);
    overSubPage=r.page;document.getElementById('overshop-sub-page').textContent=`Страница ${r.page} из ${r.pages}`;
    document.getElementById('overshop-sub-prev').disabled=r.page<=1;document.getElementById('overshop-sub-next').disabled=r.page>=r.pages;
  }catch(error){if(current===overSubRequest)document.getElementById('overshop-sub-summary').textContent=error.message;}
}
document.getElementById('overshop-sub-refresh').onclick=()=>{overSubPage=1;refreshOverSubscriptions();};
document.getElementById('overshop-sub-state').onchange=()=>{overSubPage=1;refreshOverSubscriptions();};
document.getElementById('overshop-sub-query').onkeydown=e=>{if(e.key==='Enter'){overSubPage=1;refreshOverSubscriptions();}};
document.getElementById('overshop-sub-prev').onclick=()=>{overSubPage--;refreshOverSubscriptions();};
document.getElementById('overshop-sub-next').onclick=()=>{overSubPage++;refreshOverSubscriptions();};
refreshOverSubscriptions();

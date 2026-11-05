'use strict';
const dfoPanel=document.createElement('section');dfoPanel.className='panel source-card';
dfoPanel.innerHTML='<div class="section-title"><h2>ДФО</h2><span id="dfo-status" class="badge"></span></div><p class="source-caption">Бизнес · Государственные структуры · Некоммерческие организации → ДФО</p><p>Автоматический обход каталога активных организаций. Сохраняются БИН, email, телефоны, адреса и данные источника. Один запрос раз в 10 секунд; повторный обход — через 24 часа после завершения. Пауза сохраняет прогресс.</p><div class="actions source-actions"><button id="dfo-start" class="outline source-action-primary">Включить сбор</button><button id="dfo-pause" class="outline">Пауза</button><button id="dfo-refresh" class="outline">Обновить</button></div><p id="dfo-worker" class="rule-text"></p><p id="dfo-error" role="status"></p><p id="dfo-summary" class="rule-text"></p><div id="dfo-metrics" class="stats source-metrics"></div>';
collectionPage.append(dfoPanel);
document.getElementById('journal-source').add(new Option('ДФО','dfo'));
document.getElementById('journal-source').add(new Option('Проверка email','email_check'));
let dfoBusy=false;
async function refreshDfo(){
  if(dfoBusy)return;dfoBusy=true;
  try{
    const s=await api('/api/dfo'),r=s.report;
    document.getElementById('dfo-status').textContent=s.enabled?(s.completed_at?'Ожидает обновления':s.error?'Повтор / ошибка':'Сбор включён'):'Приостановлен';
    document.getElementById('dfo-status').className='badge '+(s.enabled?'green':'');
    document.getElementById('dfo-start').disabled=s.enabled||s.busy;document.getElementById('dfo-pause').disabled=!s.enabled;
    document.getElementById('dfo-worker').textContent=(s.worker_alive?'Фоновый обработчик доступен.':'Фоновый обработчик не отвечает.')+' Страниц сохранено: '+(r.processed_pages||0)+(s.page_limit?' / '+s.page_limit:r.catalog_total?' / около '+Math.ceil(r.catalog_total/15):'')+' · Следующая страница: '+s.next_page+(s.next_attempt_at?' · Следующий запрос: '+new Date(s.next_attempt_at).toLocaleString('ru-RU',{timeZone:'Asia/Qyzylorda'}):'');
    document.getElementById('dfo-error').textContent=s.error;
    document.getElementById('dfo-summary').textContent='Создано '+(r.created||0)+', дополнено '+(r.updated||0)+', альтернативных полей '+(r.conflicts||0)+', замечаний '+(r.issues||0)+'.'+(r.catalog_total?' В каталоге источника: '+r.catalog_total+'.':'');
    const metrics=document.getElementById('dfo-metrics');metrics.replaceChildren();
    for(const [label,value]of [['Организаций',r.organizations],['Email',r.emails],['Телефонов',r.phones]]){const article=document.createElement('article'),span=document.createElement('span'),strong=document.createElement('strong');span.textContent=label;strong.textContent=value||0;article.append(span,strong);metrics.append(article);}
  }catch(error){document.getElementById('dfo-error').textContent=error.message;}finally{dfoBusy=false;}
}
async function dfoAction(action){try{await api('/api/dfo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,mode:action==='start'?'automatic':undefined})});await refreshDfo();}catch(error){document.getElementById('dfo-error').textContent=error.message;}}
document.getElementById('dfo-start').onclick=()=>dfoAction('start');document.getElementById('dfo-pause').onclick=()=>dfoAction('pause');document.getElementById('dfo-refresh').onclick=refreshDfo;
collectionButton.addEventListener('click',refreshDfo);setInterval(()=>{if(!collectionPage.hidden)refreshDfo();},5000);

'use strict';
(()=>{
const button=[...document.querySelectorAll('aside nav button')].find(b=>b.textContent.trim()==='Шаблоны');button.disabled=false;
const page=document.createElement('main');page.hidden=true;
page.innerHTML=`<div class="breadcrumb">Коммуникации / Шаблоны</div><div class="heading"><div><h1>Шаблоны писем</h1><p>HTML-редактор и предпросмотр</p></div><button class="outline" id="tpl-new">Новый шаблон</button></div>
<section class="panel"><h2>Библиотека шаблонов</h2><div id="tpl-library" class="tpl-library"></div><p>Выберите заготовку, настройте блоки и сохраните своё письмо.</p></section><section class="panel"><h2>Мои шаблоны</h2><label>Поиск по названию <input type="search" id="tpl-search"></label><button class="outline" id="tpl-refresh">Обновить список</button><p id="tpl-count"></p><div id="tpl-list" class="table-scroll"></div></section>
<section class="panel" id="tpl-editor" hidden><h2 id="tpl-title">Новый шаблон</h2><form id="tpl-form"><div class="tpl-fields"><label>Название<input id="tpl-name" required maxlength="160"></label><label>Тема письма<input id="tpl-subject" maxlength="250"></label><label>Прехедер<input id="tpl-preheader" maxlength="250"></label></div>
<div class="tpl-save-actions"><button class="primary" id="tpl-save">Сохранить</button> <button class="outline" type="button" id="tpl-copy">Сохранить копию</button></div><label class="tpl-file-field">Загрузить HTML <input id="tpl-file" type="file" accept=".html,.htm,text/html"></label>
<p><button type="button" class="outline" id="tpl-visual-mode">Визуальные блоки</button> <button type="button" class="outline" id="tpl-html-mode">HTML-код</button></p><p id="tpl-mode-note"></p><div class="tpl-work"><div><div id="tpl-builder"></div><label id="tpl-code-wrap">HTML-код<textarea id="tpl-html" maxlength="500000" spellcheck="false" aria-label="HTML-код письма"></textarea></label></div><div><div class="tpl-tools"><b>Предпросмотр</b><button type="button" class="outline" id="tpl-desktop" aria-pressed="true">Компьютер</button><button type="button" class="outline" id="tpl-mobile" aria-pressed="false">Телефон</button></div><label><input type="checkbox" id="tpl-images"> Показывать внешние изображения</label><p id="tpl-preview-subject"></p><p id="tpl-preview-preheader"></p><div class="tpl-preview"><iframe title="Предпросмотр письма" sandbox="" referrerpolicy="no-referrer"></iframe></div><small>Скрипты и формы отключены. Внешние картинки загружаются только при включении переключателя. Ссылка отписки — заготовка для будущей отправки. Отображение в почтовых программах может отличаться.</small></div></div></form></section><p role="status" id="tpl-status"></p>`;
document.body.append(page);
const css=document.createElement('style');css.textContent=`.tpl-save-actions{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin:18px 0 12px}#tpl-form .tpl-save-actions>button{display:inline-flex;align-items:center;justify-content:center;width:auto;flex:0 0 auto;margin:0;min-height:36px;padding:8px 18px}.tpl-file-field{display:block;margin:0 0 14px}.tpl-fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.tpl-fields input{width:100%;box-sizing:border-box}.tpl-work{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px}.tpl-work textarea{width:100%;min-height:560px;box-sizing:border-box;font:13px/1.6 monospace;padding:12px;resize:vertical}.tpl-tools{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.tpl-preview{overflow:auto;background:#edf0f3;padding:12px}.tpl-preview iframe{display:block;width:100%;height:520px;background:white;border:0;margin:auto;pointer-events:auto}.tpl-preview.mobile iframe{width:375px;max-width:none}.tpl-library,.tpl-block-palette{display:flex;flex-wrap:wrap;gap:8px}.tpl-library button{padding:18px}.tpl-builder-columns{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px}.tpl-block-row{border:1px solid #dde5ec;border-radius:5px;padding:8px;margin:8px 0}.tpl-block-row.selected{border-color:#0088cc;background:#edf7fc}.tpl-block-row button{margin:2px;font-size:12px}.tpl-block-settings label,.tpl-brand-settings label{display:block;margin:8px 0}.tpl-block-settings input,.tpl-brand-settings input,.tpl-block-settings textarea,.tpl-brand-settings select{display:block;width:100%;box-sizing:border-box}.tpl-block-settings textarea{min-height:100px}.tpl-work{grid-template-columns:minmax(0,1.25fr) minmax(0,1fr)}#tpl-editor{margin-top:20px}#tpl-list button{margin:4px}@media(max-width:1100px){.tpl-work,.tpl-fields{grid-template-columns:1fr}}`;css.textContent+=`#tpl-form input:focus,#tpl-form textarea:focus,#tpl-form select:focus{outline:none;border-color:#6BAEE0;box-shadow:0 0 0 2px #0795e918}#tpl-form .tpl-color-line{display:flex;gap:8px;align-items:center}#tpl-form .tpl-color-line input{flex:1;min-width:0}#tpl-form .tpl-color-trigger{width:36px;height:36px;min-width:36px;flex:none;padding:0;border:1px solid #cbd5e1;border-radius:5px}#tpl-form .tpl-color-palette:not([hidden]){display:grid;grid-template-columns:repeat(5,28px);gap:7px;padding:10px;margin-top:6px;border:1px solid #dce5ee;border-radius:6px;background:white;box-shadow:0 3px 10px #152b3c12}#tpl-form .tpl-color-palette button{width:28px;height:28px;padding:0;border:1px solid #cbd5e1;border-radius:4px}#tpl-form .tpl-color-palette button:focus-visible,#tpl-form .tpl-color-trigger:focus-visible{outline:2px solid #6BAEE0;outline-offset:2px}`;css.textContent+=`.tpl-assistant{margin:14px 0}.tpl-assistant details{border:1px solid #dce5ee;border-radius:6px;padding:12px;margin:10px 0}.tpl-assistant summary{cursor:pointer;font-weight:600}.tpl-assistant label{display:block;margin:8px 0}.tpl-assistant textarea{width:100%;min-height:90px;box-sizing:border-box}.tpl-assistant .tpl-tool-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}.tpl-tool-grid input,.tpl-tool-grid select{width:100%}.tpl-check-error{color:#b42318}.tpl-check-warning{color:#916100}.tpl-assistant p{font-size:12px}`;css.textContent+='@media(max-width:650px){.tpl-builder-columns{grid-template-columns:1fr}}';document.head.append(css);
const $=id=>document.getElementById('tpl-'+id);let current=null,dirty=false,loading=0,saving=false;
const builder=TemplateBuilder.mount($('builder'),d=>{dirty=true;$('html').value=TemplateBuilder.render(d,$('preheader').value);preview();});
const toolsHost=document.createElement('section');$('mode-note').after(toolsHost);
function state(){return {subject:$('subject').value,preheader:$('preheader').value,html:$('html').value,design:builder.get(),selected:builder.getSelected()};}
function blockText(){const d=builder.get(),x=d?.blocks[builder.getSelected()];return x?(x.type==='heading'?x.title:x.text||''):'';}
function applyText(target,text,append=false){
 const limit=target==='subject'||target==='preheader'?250:target==='html'?500000:10000;
 if(target==='subject'||target==='preheader'||target==='html'){
  const el=$(target);const value=append?el.value+' '+text:text;if(value.length>limit)throw Error('Превышен лимит поля. Сократите текст.');
  if(target==='html'&&builder.get())throw Error('Для HTML перейдите в режим кода. Для блочного письма выберите блок.');
  el.value=value;el.dispatchEvent(new Event('input',{bubbles:true}));
 }else{
  const d=builder.get();if(!d)throw Error('Для применения к блоку нужен визуальный шаблон. В HTML-режиме используйте тему или прехедер.');
  let index=builder.getSelected();
  if(target==='new'){if(d.blocks.length>=100)throw Error('В шаблоне уже 100 блоков.');d.blocks.push({type:'text',text:'',align:'left'});index=d.blocks.length-1;}
  const x=d.blocks[index];if(!x)throw Error('Выберите текстовый блок.');
  const key=x.type==='heading'?'title':['text','product','footer'].includes(x.type)?'text':null;
  if(!key)throw Error('Выберите заголовок, текст, карточку товара или подвал.');
  const value=append?(x[key]||'')+' '+text:text;if(value.length>10000)throw Error('Текст блока длиннее 10 000 символов.');
  x[key]=value;builder.set(d,index);$('html').value=TemplateBuilder.render(d,$('preheader').value);dirty=true;preview();remember();
 }
}
let tools;
tools=TemplateTools.mount(toolsHost,{state,blockText,apply:applyText,preview:()=>preview()});
let visual=false;
function mode(value){visual=value&&!!builder.get();$('builder').hidden=!visual;$('code-wrap').hidden=visual;$('visual-mode').disabled=!builder.get();$('visual-mode').setAttribute('aria-pressed',String(visual));$('html-mode').setAttribute('aria-pressed',String(!visual));$('mode-note').textContent=visual?'Редактируйте блоки и оформление. HTML формируется автоматически.':'Импортированный или изменённый вручную HTML редактируется в режиме кода.';}
function usePreset(i){if(saving||!discard())return;const p=TemplateBuilder.preset(i);fill({...p,html:TemplateBuilder.render(p.design,p.preheader)});dirty=true;status('Заготовка открыта. Сохраните её как свой шаблон.');}
TemplateBuilder.presets.forEach((p,i)=>{const b=document.createElement('button');b.type='button';b.className='outline';b.textContent=p.name;b.onclick=()=>usePreset(i);$('library').append(b);});
$('visual-mode').onclick=()=>mode(true);$('html-mode').onclick=()=>mode(false);
const status=text=>$('status').textContent=text;
const initial='<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="margin:0;background:#f3f5f7"><table role="presentation" style="max-width:600px;width:100%;margin:auto;background:white"><tr><td style="padding:24px;font-family:Arial,sans-serif"><h1>Заголовок письма</h1><p>Текст вашего предложения.</p><p><a href="https://over-shop.kz/">Перейти в магазин</a></p></td></tr></table></body></html>';
function preview(){
 const allowed=$('images').checked;
 const policy="default-src 'none'; img-src data: "+(allowed?'https: http:':'')+"; style-src 'unsafe-inline'; form-action 'none'; base-uri 'none'";
 let html=TemplateTools.personalize($('html').value,tools?.values()||{},true);
 // Keep preview scrollable while preventing links from navigating the frame.
 html=html.replace(/<(a|area)\b(?:[^"'<>]|"[^"]*"|'[^']*')*>/gi,markup=>{
  const fragment=document.createElement('template');fragment.innerHTML=markup;
  const link=fragment.content.querySelector('a,area');if(!link)return markup;
  link.removeAttribute('href');link.removeAttribute('target');link.setAttribute('aria-disabled','true');link.setAttribute('tabindex','-1');
  const tag=link.outerHTML;return link.tagName==='A'?tag.slice(0,tag.lastIndexOf('</a>')):tag;
 });
 if(!allowed){
  html=html.replace(/<img\b(?:[^"'<>]|"[^"]*"|'[^']*')*>/gi,markup=>{
   const fragment=document.createElement('template');fragment.innerHTML=markup;
   const img=fragment.content.querySelector('img');if(!img)return markup;
   if((img.getAttribute('src')||'').startsWith('data:')&&!img.hasAttribute('srcset'))return markup;
   const placeholder=document.createElement('span');
   placeholder.textContent=(img.getAttribute('alt')||'Изображение')+' — внешние изображения выключены';
   placeholder.style.cssText='display:block;padding:12px;background:#edf0f3;color:#526574;font:12px/1.5 Arial,sans-serif;border:1px dashed #cbd5e1;border-radius:4px';
   return placeholder.outerHTML;
  });
 }
 page.querySelector('iframe').srcdoc='<meta http-equiv="Content-Security-Policy" content="'+policy+'">'+html;
 $('preview-subject').textContent='Тема: '+(TemplateTools.personalize($('subject').value,tools?.values()||{})||'не указана');$('preview-preheader').textContent='Прехедер: '+(TemplateTools.personalize($('preheader').value,tools?.values()||{})||'не указан');
}

function fill(row){current=row.id?row:null;for(const key of ['name','subject','preheader','html'])$(key).value=row[key]||'';builder.set(row.design||null);mode(!!row.design);$('editor').hidden=false;$('title').textContent=row.id?'Редактирование шаблона':'Новый шаблон';dirty=false;preview();remember();}
function discard(){return !dirty||confirm('Есть несохранённые изменения. Продолжить без сохранения?');}
async function list(){const seq=++loading;try{const r=await api('/api/templates?q='+encodeURIComponent($('search').value));if(seq!==loading)return;$('count').textContent=`Найдено: ${r.total}. Показано: ${r.templates.length}`;$('list').replaceChildren();if(!r.templates.length){$('list').textContent='Шаблонов пока нет. Создайте первый шаблон.';return;}const table=document.createElement('table');for(const row of r.templates){const tr=document.createElement('tr');for(const value of [row.name,row.subject||'Без темы',new Date(row.updated_at).toLocaleString('ru-RU')]){const td=document.createElement('td');td.textContent=value;tr.append(td);}const td=document.createElement('td');const b=document.createElement('button');b.className='outline';b.textContent='Открыть';b.onclick=async()=>{if(saving||!discard())return;try{fill(await api('/api/templates/'+row.id));status('Шаблон загружен.');}catch(e){status(e.message);}};td.append(b);tr.append(td);table.append(tr);}$('list').append(table);}catch(e){status(e.message);}}
async function save(copy=false){if(saving||!$('form').reportValidity())return;saving=true;$('form').inert=true;$('save').disabled=$('copy').disabled=true;const data={};for(const key of ['name','subject','preheader','html'])data[key]=$(key).value;data.design=builder.get();if(copy)data.name=data.name.slice(0,150)+' — копия';if(current&&!copy)data.revision=current.revision;try{const row=await api('/api/templates'+(current&&!copy?'/'+current.id:''),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});fill(row);status(copy?'Копия сохранена в БД.':'Шаблон сохранён в БД.');await list();}catch(e){status(e.message);}finally{saving=false;$('form').inert=false;$('save').disabled=$('copy').disabled=false;}}
$('form').onsubmit=e=>{e.preventDefault();save();};$('copy').onclick=()=>save(true);$('new').onclick=()=>{if(!saving&&discard()){const p=TemplateBuilder.preset(0);p.name='';fill({...p,html:TemplateBuilder.render(p.design,p.preheader)});status('Новый шаблон ещё не сохранён.');}};
for(const key of ['name','subject','preheader','html'])$(key).oninput=()=>{dirty=true;if(key==='html'){builder.set(null);mode(false);}else if(key==='preheader'&&builder.get())$('html').value=TemplateBuilder.render(builder.get(),$('preheader').value);preview();};
$('file').onchange=async()=>{const file=$('file').files[0];try{if(!file)return;if(file.size>1500000)throw Error('Файл слишком большой. Максимум 1,5 МБ.');const text=await file.text();if(text.length>500000)throw Error('HTML должен содержать не более 500 000 символов.');if(!discard())return;$('html').value=text;builder.set(null);mode(false);dirty=true;preview();remember();status('HTML загружен в редактор. Сохраните шаблон.');}catch(e){status(e.message);}finally{$('file').value='';}};
for(const mode of ['desktop','mobile'])$(mode).onclick=()=>{page.querySelector('.tpl-preview').classList.toggle('mobile',mode==='mobile');for(const m of ['desktop','mobile'])$(m).setAttribute('aria-pressed',String(m===mode));};
$('images').onchange=preview;$('refresh').onclick=list;let timer;$('search').oninput=()=>{clearTimeout(timer);timer=setTimeout(list,250);};
document.querySelector('aside nav').addEventListener('click',event=>{const target=event.target.closest('button');if(!target||target.disabled)return;if(target===button){try{sessionStorage.setItem('templates-open','1');}catch{}document.querySelectorAll('body>main').forEach(p=>p.hidden=true);page.hidden=false;document.querySelectorAll('aside nav button').forEach(b=>{b.classList.remove('active');b.removeAttribute('aria-current');});button.classList.add('active');button.setAttribute('aria-current','page');document.title='Шаблоны — Рассылки';list();}else{try{sessionStorage.removeItem('templates-open');}catch{}page.hidden=true;button.classList.remove('active');button.removeAttribute('aria-current');}});
function remember(){
 if($('editor').hidden)return true;
 try{
  const row={id:current?.id,revision:current?.revision,design:builder.get()};
  for(const key of ['name','subject','preheader','html'])row[key]=$(key).value;
  sessionStorage.setItem('template-editor-state',JSON.stringify({row,dirty,visual,mobile:page.querySelector('.tpl-preview').classList.contains('mobile')}));return true;
 }catch{return false;}
}
page.addEventListener('input',()=>remember());
page.addEventListener('click',()=>queueMicrotask(remember));
window.addEventListener('pagehide',remember);
window.addEventListener('beforeunload',event=>{if(!remember()&&dirty){event.preventDefault();event.returnValue='';}});
(async()=>{
 let open=false,saved;
 try{open=sessionStorage.getItem('templates-open')==='1';saved=JSON.parse(sessionStorage.getItem('template-editor-state')||'null');}catch{}
 if(!open)return;
 button.click();
 if(!saved?.row)return;
 try{
  const row=saved.dirty||!saved.row.id?saved.row:await api('/api/templates/'+saved.row.id);
  fill(row);dirty=!!saved.dirty;mode(saved.visual);
  if(saved.mobile)$('mobile').click();
  remember();status(dirty?'Восстановлен несохранённый черновик этой вкладки. Нажмите «Сохранить», чтобы записать его в БД.':'Открытый шаблон восстановлен.');
 }catch(e){status('Не удалось восстановить шаблон: '+e.message);}
})();
})();

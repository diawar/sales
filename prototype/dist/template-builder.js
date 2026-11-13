'use strict';
window.TemplateBuilder=(()=>{
const names={logo:'Логотип и название',heading:'Заголовок',text:'Текст',image:'Изображение',button:'Кнопка',divider:'Разделитель',spacer:'Отступ',product:'Карточка товара',footer:'Контакты и отписка'};
const brand=()=>({name:'OVER',website:'https://over-shop.kz/',logo:'',contacts:'Укажите адрес и контакты отправителя',accent:'#0088cc',background:'#f3f5f7',surface:'#ffffff',text:'#152b3c',font:'Arial',width:600});
const block=type=>({type,title:type==='product'?'Название товара':'Заголовок письма',text:type==='footer'?'Вы получили это письмо по подписке.':'Текст вашего предложения.',url:'https://over-shop.kz/',image:'',alt:'Описание изображения',label:type==='product'?'Посмотреть товар':'Перейти в магазин',price:'Укажите цену',align:'left',height:'24'});
const presets=[
 {name:'Акция',subject:'Предложения Over-shop',preheader:'Выберите подходящее предложение',types:['logo','heading','image','text','button','footer'],title:'Предложения для вас'},
 {name:'Подборка товаров',subject:'Компьютеры и комплектующие',preheader:'Подборка от OVER',types:['logo','heading','product','product','button','footer'],title:'Компьютеры и комплектующие'},
 {name:'Предложение для бизнеса',subject:'Решения для вашей компании',preheader:'Обсудим комплектацию и условия',types:['logo','heading','text','divider','text','button','footer'],title:'Техника для вашей компании'},
 {name:'Возвращение покупателя',subject:'Рады видеть вас снова',preheader:'Посмотрите, что появилось в магазине',types:['logo','heading','text','product','button','footer'],title:'Рады видеть вас снова'}
];
const escape=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const text=v=>escape(v).replace(/\n/g,'<br>');
const url=v=>/^(https?:\/\/|mailto:|tel:)/i.test(v||'')?escape(v):'';
function render(d,preheader=''){
const b=d.brand;const image=(src,alt,width=b.width-48)=>url(src)?`<img src="${url(src)}" alt="${escape(alt)}" width="${width}" style="max-width:100%;height:auto;border:0;display:block">`:`<div style="padding:24px;background:#edf0f3;color:#526574">${escape(alt||'Добавьте изображение')}</div>`;
const button=(href,label)=>`<a href="${url(href)}" style="display:inline-block;background:${escape(b.accent)};color:#ffffff;text-decoration:none;padding:14px 24px;border-radius:4px">${escape(label)}</a>`;
const rows=d.blocks.map(x=>{
let inner='';switch(x.type){
case 'logo':inner=(b.logo?image(b.logo,b.name,96):'')+`<strong style="font-size:24px;color:${escape(b.accent)}">${escape(b.name)}</strong>`;break;
case 'heading':inner=`<h1 style="font-size:28px;line-height:1.3;margin:0">${text(x.title)}</h1>`;break;
case 'text':inner=`<p style="margin:0;line-height:1.6">${text(x.text)}</p>`;break;
case 'image':inner=image(x.image,x.alt);break;
case 'button':inner=button(x.url,x.label);break;
case 'divider':inner='<hr style="border:0;border-top:1px solid #dce3e9;margin:0">';break;
case 'spacer':inner=`<div style="height:${Math.max(4,Math.min(120,Number(x.height)||24))}px;line-height:1px">&nbsp;</div>`;break;
case 'product':inner=image(x.image,x.alt)+`<h2 style="font-size:22px">${text(x.title)}</h2><p>${text(x.text)}</p><p><strong>${text(x.price)}</strong></p>`+button(x.url,x.label);break;
case 'footer':inner=`<p style="font-size:12px;line-height:1.5">${text(x.text)}<br>${text(b.contacts)}<br><a href="${url(b.website)}">${escape(b.name)}</a><br><a href="{{unsubscribe_url}}">Отписаться от рассылки</a></p>`;break;
}return `<tr><td style="padding:12px 24px;text-align:${escape(x.align||'left')}">${inner}</td></tr>`;
}).join('');
return `<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="margin:0;padding:16px 0;background:${b.background};color:${b.text};font-family:${b.font},sans-serif"><div style="display:none;max-height:0;overflow:hidden">${escape(preheader)}</div><table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center"><table role="presentation" width="${b.width}" cellspacing="0" cellpadding="0" style="width:100%;max-width:${b.width}px;background:${b.surface}">${rows}</table></td></tr></table></body></html>`;
}
function mount(host,onChange,onPreset){
let design=null,selected=0;
host.innerHTML=`<div class="tpl-builder-columns"><div><h3>Добавить блок</h3><div class="tpl-block-palette"></div><h3>Блоки письма</h3><div class="tpl-block-list"></div></div><div><h3>Настройки блока</h3><div class="tpl-block-settings"></div><h3>Фирменное оформление</h3><div class="tpl-brand-settings"></div></div></div>`;
const palette=host.querySelector('.tpl-block-palette'),list=host.querySelector('.tpl-block-list'),settings=host.querySelector('.tpl-block-settings'),brandSettings=host.querySelector('.tpl-brand-settings');
function change(){onChange(structuredClone(design));}
function action(label,fn,parent){const b=document.createElement('button');b.type='button';b.className='outline';b.textContent=label;b.onclick=fn;parent.append(b);return b;}
function field(parent,label,key,target,type='text',choices=null){
const hex=type==='color';
const wrap=document.createElement('label');wrap.textContent=label+(hex?' (HEX)':'');
const input=document.createElement(choices?'select':type==='textarea'?'textarea':'input');
if(choices)choices.forEach(v=>input.add(new Option(v,v)));else if(type!=='textarea')input.type=hex?'text':type;
input.value=hex?String(target[key]||'').toUpperCase():target[key]??'';
if(!choices)input.maxLength=hex?7:type==='textarea'?10000:2000;
let palette;
if(hex){
 input.required=true;input.pattern='#[0-9A-Fa-f]{6}';input.placeholder='#0088CC';input.title='Цвет в формате HEX: # и 6 символов, например #0088CC';input.autocomplete='off';input.spellcheck=false;
 const line=document.createElement('div');line.className='tpl-color-line';
 palette=document.createElement('button');palette.type='button';palette.className='tpl-color-trigger';palette.style.backgroundColor=input.value;palette.setAttribute('aria-label','Палитра: '+label);palette.setAttribute('aria-expanded','false');
 const colors=document.createElement('div');colors.className='tpl-color-palette';colors.hidden=true;
 for(const color of ['#0088CC','#0795E9','#152B3C','#FFFFFF','#F3F5F7','#000000','#64748B','#CBD5E1','#DC2626','#F97316','#F59E0B','#FDE047','#16A34A','#14B8A6','#06B6D4','#2563EB','#4F46E5','#9333EA','#DB2777','#FDA4AF']){
  const chip=document.createElement('button');chip.type='button';chip.style.backgroundColor=color;chip.title=color;chip.setAttribute('aria-label',label+' '+color);chip.onclick=()=>{input.value=color;input.oninput();colors.hidden=true;palette.setAttribute('aria-expanded','false');palette.focus();};colors.append(chip);
 }
 palette.onclick=()=>{colors.hidden=!colors.hidden;palette.setAttribute('aria-expanded',String(!colors.hidden));};
 line.append(input,palette);wrap.append(line,colors);
}else wrap.append(input);
input.oninput=()=>{if(hex){input.value=input.value.toUpperCase();const valid=/^#[0-9A-F]{6}$/.test(input.value);input.setCustomValidity(valid?'':'Введите HEX: # и 6 символов, например #0088CC');if(!valid)return;palette.style.backgroundColor=input.value;}target[key]=type==='number'?Math.max(400,Math.min(800,Number(input.value)||600)):input.value;change();};
parent.append(wrap);
}

function redraw(){
palette.replaceChildren();list.replaceChildren();settings.replaceChildren();brandSettings.replaceChildren();if(!design)return;
for(const [type,name]of Object.entries(names))action('+ '+name,()=>{if(design.blocks.length>=100)return;design.blocks.push(block(type));selected=design.blocks.length-1;redraw();change();},palette);
design.blocks.forEach((x,i)=>{const row=document.createElement('div');row.className='tpl-block-row'+(i===selected?' selected':'');action((i+1)+'. '+names[x.type],()=>{selected=i;redraw();},row);
for(const [label,offset]of [['↑',-1],['↓',1]]){const b=action(label,()=>{const j=i+offset;[design.blocks[i],design.blocks[j]]=[design.blocks[j],design.blocks[i]];selected=j;redraw();change();},row);b.disabled=i+offset<0||i+offset>=design.blocks.length;b.setAttribute('aria-label',label==='↑'?'Поднять блок '+(i+1):'Опустить блок '+(i+1));}
action('Копировать',()=>{if(design.blocks.length>=100)return;design.blocks.splice(i+1,0,structuredClone(x));selected=i+1;redraw();change();},row);
action('Удалить',()=>{design.blocks.splice(i,1);selected=Math.max(0,Math.min(selected,design.blocks.length-1));redraw();change();},row);list.append(row);});
const x=design.blocks[selected];if(x){const fields={heading:['title'],text:['text'],image:['image','alt'],button:['label','url'],spacer:['height'],product:['title','text','price','image','alt','label','url'],footer:['text'],logo:[],divider:[]}[x.type];const labels={title:'Заголовок блока',text:'Текст блока',image:'URL изображения (HTTPS)',alt:'Подпись изображения',label:'Текст кнопки',url:'Ссылка кнопки',price:'Цена / условия',height:'Высота отступа (4–120 px)'};fields.forEach(k=>field(settings,labels[k],k,x,k==='text'?'textarea':'text'));field(settings,'Выравнивание','align',x,'text',['left','center','right']);}else settings.textContent='Добавьте первый блок.';
for(const [key,label]of [['name','Название компании'],['website','Сайт компании'],['logo','URL логотипа (HTTPS)'],['contacts','Адрес и контакты отправителя'],['accent','Основной цвет'],['background','Фон письма'],['surface','Фон содержимого'],['text','Цвет текста'],['font','Шрифт'],['width','Ширина письма (400–800 px)']])field(brandSettings,label,key,design.brand,['accent','background','surface','text'].includes(key)?'color':key==='width'?'number':'text',key==='font'?['Arial','Verdana','Georgia']:null);
}
return {set(value,index=0){design=value?structuredClone(value):null;selected=index;redraw();},getSelected(){return selected;},get(){return design?structuredClone(design):null;}};
}
function preset(index){const p=presets[index];return {name:p.name,subject:p.subject,preheader:p.preheader,design:{version:1,brand:brand(),blocks:p.types.map(t=>{const x=block(t);if(t==='heading')x.title=p.title;return x;})}};}
return {render,mount,preset,presets,names};
})();

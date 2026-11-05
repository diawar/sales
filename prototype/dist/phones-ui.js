'use strict';
function installPhones(){
  const grid=document.querySelector('.filter-grid'),label=document.createElement('label');label.textContent='Тип телефона';
  const select=document.createElement('select');select.id='phone_kind';
  select.innerHTML='<option value="">Любой тип</option><option value="mobile">Сотовый</option><option value="landline">Городской</option><option value="service">Сервисный</option><option value="unknown">Не определён</option>';
  label.append(select);grid.append(label);
  const searchLabel=document.createElement('label');searchLabel.textContent='Номер телефона';const input=document.createElement('input');input.id='phone';input.placeholder='Номер или его часть';searchLabel.append(input);grid.append(searchLabel);
  addSortHeader('phone','Телефоны');render();
}

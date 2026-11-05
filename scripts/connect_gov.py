from pathlib import Path
def change(path,old,new):
    p=Path(path);text=p.read_text(encoding='utf-8')
    if old not in text:raise ValueError(f'Expected fragment missing: {path}')
    p.write_text(text.replace(old,new),encoding='utf-8')
change('prototype/dist/database.js',"await loadScript('/source-layout.js');","await loadScript('/source-layout.js');await loadScript('/gov.js');")
change('prototype/dist/database.js',"ЭТАП 2.2b","ЭТАП 2.2c")
change('contacts/views.py',"'eondiris.js','source-layout.js'","'eondiris.js','source-layout.js','gov.js'")
change('prototype/dist/source-layout.js','<option value="eondiris">e-ondiris</option>','<option value="eondiris">e-ondiris</option><option value="gov">Gov.kz</option>')
change('prototype/dist/source-layout.js',"eondiris:'e-ondiris'}","eondiris:'e-ondiris',gov:'Gov.kz'}")
change('prototype/dist/phones-ui.js','<option value="unknown">Не определён</option>','<option value="service">Сервисный</option><option value="unknown">Не определён</option>')
change('prototype/dist/phones-ui.js',"landline:'Городской',unknown:","landline:'Городской',service:'Сервисный',unknown:")
change('prototype/dist/profile-ui.js',"production_address:'Адрес производства'","gov_contact:'Контакты госоргана',phone_purpose:'Назначение телефона',production_address:'Адрес производства'")

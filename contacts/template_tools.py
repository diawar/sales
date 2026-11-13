"""Local template diagnostics and literal (non-executable) personalization."""
import html
import re
from urllib.parse import urlsplit
from bs4 import BeautifulSoup

VARIABLES={'name','company','city','email','unsubscribe_url'}
TOKEN=re.compile(r'{{\s*([a-z_]+)\s*(?:\|([^{}]*))?}}')

def personalize(text, values, html_mode=False):
    issues=[]
    def replace(match):
        name,fallback=match.groups()
        if name not in VARIABLES:
            issues.append('Неизвестная переменная: '+name);return match.group(0)
        value='https://example.com/unsubscribe-preview' if name=='unsubscribe_url' else values.get(name,'').strip() or (fallback or '').strip()
        if not value:issues.append('Нет значения и запасного текста: '+name)
        return html.escape(value,quote=True) if html_mode else value
    rendered=TOKEN.sub(replace,text)
    if '{{' in TOKEN.sub('',text) or '}}' in TOKEN.sub('',text):issues.append('Некорректная запись переменной.')
    return rendered,sorted(set(issues))

def check_template(subject,preheader,source,values):
    findings=[]
    def add(level,code,message):
        if not any(f['code']==code for f in findings):findings.append({'level':level,'code':code,'message':message})
    rendered,issues=personalize(source,values,True)
    for value in (subject,preheader):issues+=personalize(value,values)[1]
    for issue in sorted(set(issues)):add('error' if 'Неизвестная' in issue or 'Некорректная' in issue else 'warning',issue,issue)
    if not subject.strip():add('error','subject','Не указана тема письма.')
    if len(subject)>70:add('warning','long_subject','Тема длиннее 70 символов: часть может обрезаться.')
    if not preheader.strip():add('warning','preheader','Добавьте прехедер — краткое описание письма.')
    soup=BeautifulSoup(source,'html.parser')
    if not soup.get_text(' ',strip=True):add('error','empty','Нет текста письма.')
    if not any(re.fullmatch(r'{{\s*unsubscribe_url\s*}}',a.get('href','')) for a in soup.find_all('a')):
        add('error','unsubscribe','Добавьте ссылку отписки: href="{{unsubscribe_url}}". Рабочий адрес будет подставлен при подключении отправки.')
    if soup.find(['script','iframe','form','object','embed','input','button','video','audio']):add('error','active','В письме есть скрипты, формы или неподдерживаемое активное содержимое.')
    for tag in soup.find_all(True):
        if any(k.lower().startswith('on') or k.lower()=='srcdoc' for k in tag.attrs):add('error','events','Удалите обработчики событий и srcdoc из HTML.')
        if tag.name=='meta' and str(tag.get('http-equiv','')).lower()=='refresh':add('error','refresh','Удалите автоматический переход (meta refresh).')
        if tag.name=='img' and not str(tag.get('alt','')).strip():add('warning','alt','Есть изображения без описания alt.')
        for attr in ('href','src'):
            value=str(tag.get(attr,'')).strip()
            if attr not in tag.attrs:continue
            if TOKEN.search(value):continue
            if not value or value=='#':add('warning','empty_link','Есть пустая ссылка или адрес изображения.');continue
            try:parsed=urlsplit(value);scheme=parsed.scheme.lower()
            except ValueError:add('error','bad_url','Есть некорректный URL.');continue
            if scheme not in ('https','http','mailto','tel') or (scheme in ('http','https') and not parsed.netloc):add('error','unsafe_url','Есть относительный или небезопасный адрес. Используйте полный HTTPS-адрес.')
            if scheme=='http':add('warning','http','Есть адреса HTTP. Лучше заменить их на HTTPS.')
    # Validate substituted attribute values too (a variable must not create an executable URL).
    for tag in BeautifulSoup(rendered,'html.parser').find_all(True):
        for attr in ('href','src'):
            value=str(tag.get(attr,'')).strip()
            if value and re.match(r'(?is)^\s*(javascript|vbscript|data)\s*:',value):add('error','variable_url','После подстановки данных получается небезопасный адрес.')
    if len(source.encode('utf-8'))>100*1024:add('warning','size','HTML больше 100 КБ: некоторые почтовые программы могут обрезать письмо.')
    if re.search(r'Укажите (цену|адрес)|Текст вашего предложения|Название товара|Добавьте изображение',soup.get_text(' ',strip=True)):add('warning','placeholders','В письме остался текст заготовки. Замените его перед отправкой.')
    if not soup.find('meta',attrs={'name':'viewport'}):add('warning','viewport','Нет настройки viewport для мобильного отображения.')
    return {'findings':findings,'errors':sum(f['level']=='error' for f in findings),'warnings':sum(f['level']=='warning' for f in findings),'note':'Локальная проверка структуры. Доступность сайтов, существование ящиков и попадание во входящие не проверяются.'}

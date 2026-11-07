"""Importers only: no database state. They must never invent schedule entries."""
from __future__ import annotations
import os
import re
import hashlib
import requests
from bs4 import BeautifulSoup
from datetime import datetime, date, timedelta
from urllib.parse import urljoin
from dataclasses import dataclass

MAI_URL = 'https://kaf406.ru/Schedule/ViewScheduleToday'
MGSU_WEB_URL = 'https://raspika.com/raspisanie/mgsu/%D0%98%D0%9F%D0%93%D0%A1-2-64-so'
RASPIKA_API = 'https://raspika.com/api/v1'
MONTHS = {m:i for i,m in enumerate(['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'],1)}
DAY_RE = re.compile(r'(?:понедельник|вторник|среда|четверг|пятница|суббота|воскресенье),?\s+(\d{1,2})\s+([а-яё]+)(?:\s+(20\d{2}))?',re.I)
TIME_RE = re.compile(r'\b([01]?\d|2[0-3]):([0-5]\d)\b')
MAI_LINE = re.compile(r'^(ЛК|ПЗ|ЛР|ПР|СЕМ|ЭКЗ|ЗАЧ|Консультация)\s+(\d{1,2}:\d{2})\s*[→\-–]+\s*(\d{1,2}:\d{2})',re.I)
KINDS = ('Лекция','Практика','Лаборатор.','Лабораторная','Семинар','Зачёт','Экзамен')

class SourceError(RuntimeError):
    pass

class EmptySchedule(SourceError):
    pass

@dataclass
class ScheduleBatch:
    events: list
    coverage_from: str
    coverage_to: str
    label: str = 'Расписание взято с raspika.com'
    limited: bool = False
    provider: str = ''
    status: str = 'ok'
    note: str = ''
    source_url: str = ''

def client_get(url, *, params=None, headers=None):
    res=requests.get(url, params=params, headers={'User-Agent':'VmesteSchedule/0.1.2 (personal schedule application)', **(headers or {})}, timeout=25)
    res.raise_for_status()
    if 'text/html' in res.headers.get('Content-Type',''):
        # Both schedule pages declare UTF-8; requests may otherwise choose Latin-1.
        res.encoding = 'utf-8'
    return res

def stable_id(slot, item):
    """Stable when rooms or end times change; date/start/topic define occurrence."""
    canonical='|'.join([slot,item['date'],item['start'], re.sub(r'\s+',' ',item['title']).strip().casefold()])
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]

def parse_date(text, year=None):
    m=DAY_RE.search(text.strip().lower())
    if not m:return None
    month=MONTHS.get(m.group(2))
    if not month:return None
    y=int(m.group(3)) if m.group(3) else (year or date.today().year)
    try:return date(y,month,int(m.group(1))).isoformat()
    except ValueError:return None

def _normalize_mai_heading(s):
    return re.sub(r'\s+',' ',s).strip()

def parse_mai(html):
    soup=BeautifulSoup(html,'html.parser')
    events=[]
    # Real MAI page uses headings for dates and (separate) lesson metadata/subjects.
    headings=soup.find_all(re.compile(r'^h[1-6]$'))
    current_date=None
    waiting=None
    for h in headings:
        s=_normalize_mai_heading(h.get_text(' ',strip=True))
        if not s:continue
        if DAY_RE.search(s):
            current_date=parse_date(s)
            waiting=None
            continue
        if not current_date:continue
        match=MAI_LINE.search(s)
        if match:
            room=s[match.end():].strip()
            room=re.sub(r'^\(\d+\s+пары\)\s*','',room)
            waiting={'date':current_date,'start':match.group(2).zfill(5),'end':match.group(3).zfill(5),
                     'title':'','lesson_type':match.group(1).upper(),'teacher':'','room':room,'subgroup':0}
            continue
        if waiting and not waiting['title'] and s:
            waiting['title']=s
            if waiting['start']<waiting['end']:
                waiting['external_id']=stable_id('mai',waiting)
                events.append(waiting)
            waiting=None
    # Avoid silently claiming a successful empty parsing result.
    if not events: raise SourceError('Не удалось распознать занятия МАИ: структура страницы изменилась')
    return events

def get_mai():
    group=os.getenv('MAI_GROUP','М3О-237БВ-25')
    response=client_get(MAI_URL,params={'AndFuture':'true','Name':group})
    return parse_mai(response.text), 'Кафедра 406 МАИ'

def _raspika_json(method, *, params, headers):
    try:
        payload=client_get(f'{RASPIKA_API}/{method}',params=params,headers=headers).json()
    except requests.HTTPError as exc:
        code=exc.response.status_code if exc.response is not None else None
        messages={401:'Raspika не приняла API-ключ. Проверьте RASPIKA_API_KEY в .env сервера.',
                  404:'Raspika не нашла заведение или группу. Данные не заменены.',
                  429:'Raspika ограничила частоту запросов. Повторите проверку позже.',
                  503:'Источник МГСУ временно недоступен, сохранённой копии Raspika нет.'}
        raise SourceError(messages.get(code,'Ошибка API Raspika. Прежние данные сохранены.')) from None
    if not isinstance(payload,dict):raise SourceError('Некорректный ответ API Raspika')
    return payload

def _api_schedule(date_from, date_to):
    key=os.getenv('RASPIKA_API_KEY','').strip()
    if not key: raise SourceError('Нужен API-ключ Raspika для устойчивого импорта МГСУ')
    headers={'X-Api-Key':key}
    inst=os.getenv('RASPIKA_INSTITUTION','mgsu')
    groups=_raspika_json('groups',params={'inst':inst,'q':os.getenv('RASPIKA_GROUP_QUERY','ИПГС 2-64')}, headers=headers)
    candidates=groups.get('data',[])
    if not isinstance(candidates,list): raise SourceError('Некорректный справочник групп API')
    matches=[g for g in candidates if re.match(r'^ИПГС[\s-]+2-64(?=\s|·|$)',g.get('name',''),re.I) and 'маг' not in g.get('name','').lower()]
    if len(matches)!=1: raise SourceError('API не определил однозначно группу ИПГС 2-64; данные не заменены')
    item=matches[0]
    params={'inst':inst,'group':item['id'],'subgroup':1,'date_from':date_from,'date_to':date_to}
    payload=_raspika_json('schedule',params=params,headers=headers)
    if not isinstance(payload.get('data'),list):raise SourceError('Некорректный ответ API расписания')
    if payload.get('stale'):
        raise SourceError('Raspika пометила расписание как устаревшее; текущие данные не заменены')
    if payload.get('inst',inst)!=inst or str(payload.get('group',item['id']))!=str(item['id']):
        raise SourceError('API вернул расписание другой группы')
    data=[]
    for lesson in payload['data']:
        subgroup=lesson.get('subgroup')
        if subgroup in (2,'2'):continue
        if subgroup not in (None,0,1,'', '0','1'):raise SourceError('Неизвестная подгруппа в ответе API')
        d=lesson.get('date','')
        a=lesson.get('start','') or ''
        b=lesson.get('end','') or ''
        subject=lesson.get('subject','')
        try:
            date.fromisoformat(d)
            datetime.strptime(a,'%H:%M'); datetime.strptime(b,'%H:%M')
            valid = bool(re.fullmatch(r'\d\d:\d\d',a) and re.fullmatch(r'\d\d:\d\d',b) and a<b and subject.strip() and date_from<=d<=date_to)
        except (ValueError,TypeError,AttributeError): valid=False
        if not valid: raise SourceError('API вернул занятие с неполными или неверными датой/временем; данные сохранены')
        i={'date':d,'start':a,'end':b,'title':subject[:200],'lesson_type':str(lesson.get('type') or '')[:45],
           'teacher':str(lesson.get('teacher') or '')[:160],'room':str(lesson.get('room') or '')[:100],
           'subgroup':int(subgroup or 0)}
        i['external_id']=stable_id('mgsu',i)
        data.append(i)
    if not data: raise EmptySchedule('Источник не вернул занятий МГСУ за запрошенный период. Это не подтверждает отсутствие пар; прежние данные сохранены')
    # The API may have less data than requested. Do not claim the entire requested
    # semester is covered merely because HTTP succeeded.
    dates=[e['date'] for e in data]
    return ScheduleBatch(list({e['external_id']:e for e in data}.values()),min(dates),max(dates))

def get_mgsu(date_from, date_to):
    """Officially documented API. Deliberately no undocumented HTML extraction fallback."""
    return _api_schedule(date_from,date_to)

def parse_raspika_batch(html, today=None):
    """Read individual lesson cards, never infer the following week's lessons."""
    soup=BeautifulSoup(html,'html.parser')
    h1=soup.find('h1')
    if not h1 or not re.search(r'ИПГС[\s-]+2-64(?=\s|·|$)',h1.get_text(' ',strip=True)):
        raise SourceError('Публичная страница не подтверждает группу ИПГС 2-64')
    ref=today or date.today()
    entries=[]; days=[]; weekday_names=['понедельник','вторник','среда','четверг','пятница','суббота','воскресенье']
    for heading in soup.find_all(['h2','h3']):
        label=heading.get_text(' ',strip=True)
        if label.startswith('Другие группы'): break
        match=DAY_RE.search(label)
        if not match: continue
        raw=parse_date(label,ref.year)
        if not raw: raise SourceError('Не удалось определить дату МГСУ')
        day=date.fromisoformat(raw)
        if not match.group(3):
            candidates=[]
            for year in (ref.year-1,ref.year,ref.year+1):
                try:candidates.append(day.replace(year=year))
                except ValueError:pass
            day=min(candidates,key=lambda d:abs((d-ref).days))
        if not label.casefold().startswith(weekday_names[day.weekday()]):
            raise SourceError('День недели не совпадает с датой на странице МГСУ')
        days.append(day)
        listing=heading.find_next_sibling()
        if listing is None or listing.name!='ul':
            raise SourceError('Изменилась структура публичной недели МГСУ')
        for card in listing.find_all('li',recursive=False):
            lines=list(card.stripped_strings)
            full=' '.join(lines)
            subgroup_match=re.search(r'подгр(?:уппа)?\.?\s*(\d+)',full,re.I)
            subgroup=int(subgroup_match.group(1)) if subgroup_match else 0
            if subgroup==2:continue
            if subgroup not in (0,1):raise SourceError('Неизвестная подгруппа на публичной странице')
            clock=card.select_one('.tm')
            times=list(clock.stripped_strings) if clock else lines[:2]
            if len(times)!=2 or not all(re.fullmatch(r'\d{1,2}:\d{2}',t) for t in times):
                raise SourceError('Не распознано время занятия МГСУ')
            a,b=[t.zfill(5) for t in times]
            try:datetime.strptime(a,'%H:%M');datetime.strptime(b,'%H:%M')
            except ValueError:raise SourceError('Неверное время занятия МГСУ')
            title_node=card.find('b'); details_node=card.find('small')
            title=title_node.get_text(' ',strip=True) if title_node else (lines[2] if len(lines)>2 else '')
            details=details_node.get_text(' ',strip=True) if details_node else ' '.join(lines[3:])
            parts=[p.strip() for p in details.split('·') if p.strip()]
            kind=parts.pop(0) if parts and parts[0] in KINDS else ''
            room='';teacher=''
            if len(parts)>1:teacher=parts[0];room=parts[-1]
            elif parts:
                if re.search(r'/|СОК',parts[0]):room=parts[0]
                else:teacher=parts[0]
            if not title or a>=b:raise SourceError('Неполное занятие на странице МГСУ')
            item={'date':day.isoformat(),'start':a,'end':b,'title':title[:200],
                  'lesson_type':kind,'teacher':teacher[:160],'room':room[:100],'subgroup':subgroup}
            item['external_id']=stable_id('mgsu',item);entries.append(item)
    if not entries:raise EmptySchedule('На публичной странице нет распознанных занятий МГСУ. Отсутствие пар не подтверждено')
    monday=min(days)-timedelta(days=min(days).weekday())
    sunday=monday+timedelta(days=6)
    if any(not monday<=d<=sunday for d in days):raise SourceError('Публичная страница содержит неоднозначный диапазон дат')
    return ScheduleBatch(list({e['external_id']:e for e in entries}.values()),monday.isoformat(),sunday.isoformat(),limited=True)

def parse_raspika_public(html, today=None):
    return parse_raspika_batch(html,today).events

def get_mgsu_public():
    response=client_get(MGSU_WEB_URL)
    return parse_raspika_batch(response.text,today=date.today())

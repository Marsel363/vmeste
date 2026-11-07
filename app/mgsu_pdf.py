"""Read the official specialist timetable by cell geometry, not text order.

A cell occupying a full lesson is weekly; its upper/lower halves are odd/even
academic weeks. Explicit week conditions and subgroup labels apply afterwards.
"""
from __future__ import annotations
import hashlib
import io
import json
import re
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse
import pdfplumber
from bs4 import BeautifulSoup
from .sources import SourceError, EmptySchedule, ScheduleBatch, client_get, stable_id

OFFICIAL_PAGE='https://mgsu.ru/student/Raspisanie_zanyatii_i_ekzamenov/fayly-raspisaniya-dlya-skachivaniya/'
ROOT=Path(__file__).resolve().parent.parent
SEED=ROOT/'data/mgsu_autumn_2026.pdf'
SEED_META=ROOT/'data/mgsu_autumn_2026.json'
DAYS=['ПОНЕДЕЛЬНИК','ВТОРНИК','СРЕДА','ЧЕТВЕРГ','ПЯТНИЦА','СУББОТА','ВОСКРЕСЕНЬЕ']
SUBGROUP=re.compile(r'(?:-\s*)?([12])\s*п\s*/\s*г\b|подгр(?:уппа)?\.?\s*([12])\b',re.I)
TEACHER=re.compile(r'(?:ст\.?\s*пр\.?|доц\.?|проф\.?|пр\.?|зав\.?\s*каф\.?)\s*([А-ЯЁ][а-яё-]+\s+[А-ЯЁ]\.\s*(?:[А-ЯЁ]\.)?)')
ROOM=re.compile(r'\b\d{2,3}(?:\s*[А-ЯA-Z])?\s*/\s*(?:КМК|КПА|УЛК|УЛБ)\b|\bСОК\b')
KIND=re.compile(r'^(лаб\.|пр\.|л\.|КОП\b|КРП\b)\s*',re.I)
KIND_NAMES={'лаб.':'Лабораторная','пр.':'Практика','л.':'Лекция','коп':'КОП','крп':'КРП'}
START_WEEK=re.compile(r'\b(?:с|со)\s+(\d+)\s+нед(?:ел[ияиь]|\.)?',re.I)
END_WEEK=re.compile(r'\bпо\s+(\d+)\s+нед(?:ел[июяь]|\.)?\s*(?:вкл\.?)?',re.I)
WEEK_LIST=re.compile(r'\b(?:на\s+)?(\d+(?:\s*,\s*\d+)*)\s+нед(?:ел[ияхюь]*|\.)?',re.I)

def clean(text):
    return re.sub(r'\s+',' ',text or '').strip()

def academic_week(day, week_one):
    return (day-week_one).days//7+1

def _weeks_and_title(text,total):
    weeks=set(range(1,total+1))
    for pattern,kind in ((START_WEEK,'start'),(END_WEEK,'end'),(WEEK_LIST,'list')):
        for m in pattern.finditer(text):
            values=[int(x) for x in m.group(1).split(',')]
            if any(x<1 or x>total for x in values):raise SourceError('В PDF указан неизвестный номер учебной недели')
            if kind=='start':weeks&=set(range(values[0],total+1))
            elif kind=='end':weeks&=set(range(1,values[0]+1))
            else:weeks&=set(values)
        text=pattern.sub('',text)
    if re.search(r'\bнед',text,re.I):raise SourceError('Не распознано условие по учебным неделям в PDF')
    return weeks,clean(text).strip(' ;,.')

def cell_variants(raw,total=16,subgroup=1):
    """Parse only explicit metadata; unknown conditions fail the import."""
    raw=raw.strip()
    if not raw or clean(raw).casefold()=='перерыв':return []
    sub=SUBGROUP.search(raw)
    sg=int(sub.group(1) or sub.group(2)) if sub else 0
    if sg and sg!=subgroup:return []
    raw=SUBGROUP.sub('',raw)
    teachers=list(TEACHER.finditer(raw))
    teacher=clean(teachers[-1].group(1)) if teachers else ''
    if len({clean(m.group(1)) for m in teachers})>1:
        raise SourceError('Несколько преподавателей в одной ячейке PDF: нужен отдельный разбор')
    raw=TEACHER.sub('',raw)
    rooms=ROOM.findall(raw)
    shared_room=clean(rooms[-1]).replace(' /','/').replace('/ ','/') if rooms else ''
    # Each explicit new lesson prefix starts an alternative. Wrapped subject
    # lines continue the current block. Lab work inherits the preceding subject.
    blocks=[]
    for line in raw.splitlines():
        line=clean(line)
        if not line:continue
        if KIND.match(line) or re.match(r'^Лаб\.?\s*работы',line,re.I):blocks.append(line)
        elif blocks:blocks[-1]+=' '+line
        else:raise SourceError('В PDF не распознан тип занятия')
    variants=[];base_subject=None
    for block in blocks:
        local_rooms=ROOM.findall(block)
        room=clean(local_rooms[-1]).replace(' /','/').replace('/ ','/') if local_rooms else shared_room
        block=ROOM.sub('',block)
        weeks,block=_weeks_and_title(block,total)
        if re.match(r'^Лаб\.?\s*работы',block,re.I):
            if not base_subject:raise SourceError('В PDF не определён предмет лабораторной работы')
            subject=base_subject;kind='Лабораторная'
        else:
            prefix=KIND.match(block)
            if not prefix:raise SourceError('В PDF не определён тип занятия')
            kind=KIND_NAMES[prefix.group(1).casefold()]
            subject=clean(block[prefix.end():]).strip(' ;,.')
            base_subject=subject
        if not subject or not weeks:raise SourceError('В PDF не удалось однозначно определить занятие')
        variants.append(dict(title=subject,lesson_type=kind,teacher=teacher,room=room,subgroup=sg,weeks=weeks))
    # Two alternatives active in the same week in this single-group cell are
    # ambiguous. Never silently invent simultaneous lessons.
    for i,a in enumerate(variants):
        if any(a['weeks']&b['weeks'] for b in variants[i+1:]):
            raise SourceError('В PDF пересекаются условия нескольких занятий в одной ячейке')
    return variants

def read_pdf(pdf_bytes):
    if not pdf_bytes.startswith(b'%PDF-') or len(pdf_bytes)>15_000_000:
        raise SourceError('Источник МГСУ вернул не PDF расписания')
    templates=[];seen_slots=set();metadata=None
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        if not 1<=len(pdf.pages)<=20:raise SourceError('Неожиданный объём PDF МГСУ')
        whole='\n'.join(p.extract_text() or '' for p in pdf.pages)
        year=re.search(r'осеннего\s+семестра\s+(20\d{2})\s*[-–]\s*(20\d{2})',whole,re.I)
        weeks=re.search(r'(\d+)\s+учебных\s+недель',whole,re.I)
        if not year or int(year.group(2))!=int(year.group(1))+1 or not weeks:
            raise SourceError('Не подтверждены семестр и число учебных недель PDF')
        total=int(weeks.group(1))
        if not 1<=total<=30:raise SourceError('Неизвестная длительность семестра PDF')
        first=date(int(year.group(1)),9,1)
        monday=first-timedelta(days=first.weekday())
        metadata=dict(term_start=first,week_one=monday,weeks=total,term_end=monday+timedelta(weeks=total)-timedelta(days=1))
        for page in pdf.pages:
            matches=[]
            for table in page.find_tables():
                data=table.extract()
                if not data:continue
                cols=[i for i,t in enumerate(data[0]) if re.search(r'ИПГС\s+2к\s+64\s+so\s+08\.05\.01',clean(t))]
                if len(cols)==1:matches.append((table,data,cols[0]))
            if len(matches)!=1:raise SourceError('В PDF не найдена однозначно колонка специалитета ИПГС 2к 64')
            table,data,column=matches[0]
            cells={tuple(row.cells[column]):data[i][column] for i,row in enumerate(table.rows) if row.cells[column] is not None}
            weekday=None
            for i,row in enumerate(table.rows):
                label=re.sub(r'\s+','',data[i][0] or '').upper()
                if label in DAYS:weekday=DAYS.index(label)
                if not data[i][1] or not str(data[i][1]).isdigit():continue
                number=int(data[i][1])
                if weekday is None or number not in range(1,7):raise SourceError('Не определены день и номер пары PDF')
                times=re.findall(r'\b(\d{2})\.(\d{2})\b',data[i][2] or '')
                if len(times)!=2:raise SourceError('Не определено время пары PDF')
                start,end=[':'.join(t) for t in times]
                if start>=end:raise SourceError('Неверное время пары PDF')
                slot=(weekday,number)
                if slot in seen_slots:raise SourceError('Повтор дня/пары в PDF')
                seen_slots.add(slot)
                y0,y1=row.cells[2][1],row.cells[2][3]
                selected=[(box,raw) for box,raw in cells.items() if box[1]>=y0-1 and box[3]<=y1+1]
                selected.sort(key=lambda pair:pair[0][1])
                if len(selected) not in (1,2):raise SourceError('Ячейка PDF разделена неожиданным образом')
                # Require exact full-cell or half-cell geometry; extra rows in
                # neighbouring groups must not alter parity in our column.
                expected=[(y0,y1)] if len(selected)==1 else [(y0,(y0+y1)/2),((y0+y1)/2,y1)]
                for index,((box,raw),(top,bottom)) in enumerate(zip(selected,expected)):
                    if abs(box[1]-top)>1 or abs(box[3]-bottom)>1:
                        raise SourceError('Не распознано деление пары на чётную и нечётную неделю')
                    parity=0 if len(selected)==1 else (1 if index==0 else 2)
                    for variant in cell_variants(raw or '',total):
                        templates.append(dict(weekday=weekday,start=start,end=end,parity=parity,**variant))
        if seen_slots!={(d,n) for d in range(5) for n in range(1,7)}:
            raise SourceError('PDF не содержит полной сетки понедельник–пятница для группы')
    return templates,metadata

def parse_pdf_schedule(pdf_bytes,date_from,date_to,*,provider='mgsu_pdf',label='Официальный PDF МГСУ',status='ok',note=''):
    templates,meta=read_pdf(pdf_bytes)
    begin=max(date.fromisoformat(date_from),meta['term_start'])
    end=min(date.fromisoformat(date_to),meta['term_end'])
    events=[];day=begin
    while day<=end:
        week=academic_week(day,meta['week_one'])
        parity=1 if week%2 else 2
        for row in templates:
            if row['weekday']!=day.weekday() or row['parity'] not in (0,parity) or week not in row['weeks']:continue
            item={k:row[k] for k in ('title','start','end','lesson_type','room','teacher','subgroup')}
            item['date']=day.isoformat();item['external_id']=stable_id('mgsu',item);events.append(item)
        day+=timedelta(days=1)
    if not events:raise EmptySchedule('PDF не содержит занятий МГСУ в выбранном периоде семестра; прежние данные сохранены')
    return ScheduleBatch(events,begin.isoformat(),end.isoformat(),label=label,provider=provider,status=status,note=note,source_url=OFFICIAL_PAGE)

def _official_pdf():
    response=client_get(OFFICIAL_PAGE)
    soup=BeautifulSoup(response.text,'html.parser')
    links=[a['href'] for a in soup.find_all('a',href=True) if re.sub(r'\s+','',a.get_text()).casefold()=='ипгсспец2курс']
    if len(set(links))!=1:raise SourceError('На сайте МГСУ не найдена однозначно ссылка на PDF ИПГС спец 2 курс')
    url=urljoin(OFFICIAL_PAGE,links[0])
    host=urlparse(url).hostname or ''
    if urlparse(url).scheme!='https' or not (host=='mgsu.ru' or host.endswith('.mgsu.ru')):
        raise SourceError('Ссылка расписания ведёт за пределы официального сайта МГСУ')
    pdf=client_get(url)
    return pdf.content,soup.get_text(' ',strip=True)

def get_pdf_schedule(date_from,date_to):
    try:
        pdf_bytes,page_text=_official_pdf()
        templates,meta=read_pdf(pdf_bytes)
        anchor=re.search(r'(\d+)\s+(?:НЕЧ[ЕЁ]ТНАЯ|Ч[ЕЁ]ТНАЯ)\s+НЕДЕЛЯ\s*\((\d{2}\.\d{2}\.20\d{2})',page_text,re.I)
        if anchor:
            from datetime import datetime
            anchor_day=datetime.strptime(anchor.group(2),'%d.%m.%Y').date()
            if academic_week(anchor_day,meta['week_one'])!=int(anchor.group(1)):
                raise SourceError('Нумерация недель PDF не совпала с официальной страницей')
        return parse_pdf_schedule(pdf_bytes,date_from,date_to,note='Базовая семестровая сетка из официального PDF. Разовые отмены и переносы сверяйте с объявлениями вуза.')
    except Exception as exc:
        # The attached official document is a dated fallback, never silently
        # presented as a successful fresh download from the university.
        try:
            data=SEED.read_bytes();meta=json.loads(SEED_META.read_text(encoding='utf-8'))
            if hashlib.sha256(data).hexdigest()!=meta['sha256']:raise SourceError('Не совпала контрольная сумма сохранённого PDF')
            return parse_pdf_schedule(data,date_from,date_to,status='baseline',label='Сохранённый официальный PDF МГСУ',note=f'Официальный сайт не удалось обновить. Базовая сетка из PDF от {meta["verified_on"]}; разовые изменения не подтверждены.')
        except Exception:
            raise SourceError('Не удалось прочитать актуальный или сохранённый PDF МГСУ; прежние данные сохранены') from None

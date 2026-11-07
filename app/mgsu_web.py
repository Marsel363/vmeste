"""Fallback for the site's ordinary public browser/guest interface.

No Telegram credentials, development headers or private user data are used.
The university PDF remains the primary source of the regular semester grid.
"""
from __future__ import annotations
import re
import uuid
from datetime import date, datetime, timedelta
from .sources import client_get, SourceError, EmptySchedule, ScheduleBatch, stable_id
from .mgsu_pdf import KIND, KIND_NAMES, SUBGROUP, clean

BASE='https://raspisanie-mgsu.ru'
_GUEST=str(uuid.uuid4())

def _json(method,params=None):
    return client_get(BASE+'/api/'+method,params=params,headers={'X-Web-Guest-Id':_GUEST}).json()

def select_group(groups):
    if not isinstance(groups,list):raise SourceError('Неверный справочник групп raspisanie-mgsu.ru')
    matches=[g for g in groups if g.get('university_id')=='mgsu' and g.get('program_track')=='specialist'
             and re.fullmatch(r'ИПГС\s+спец\s+2-64',g.get('group_name',''),re.I)]
    if len(matches)!=1:raise SourceError('На raspisanie-mgsu.ru не определена однозначно группа ИПГС спец 2-64')
    return matches[0]['id']

def parse_day(payload,day):
    if not isinstance(payload,dict) or payload.get('date')!=day or payload.get('has_schedule') is not True or not isinstance(payload.get('lessons'),list):
        raise SourceError('raspisanie-mgsu.ru не подтвердил расписание выбранного дня')
    if payload.get('week_parity') not in ('odd','even'):raise SourceError('Источник не подтвердил чётность недели')
    out=[]
    for row in payload['lessons']:
        if not isinstance(row,dict):raise SourceError('Неизвестный формат занятия raspisanie-mgsu.ru')
        title=clean(row.get('subject'))
        sub=SUBGROUP.search(title)
        sg=int(sub.group(1) or sub.group(2)) if sub else 0
        if sg==2:continue
        parity=row.get('week_parity')
        if parity not in ('all',payload['week_parity']):raise SourceError('Источник вернул занятие другой чётности')
        start=str(row.get('time') or '').zfill(5);end=str(row.get('time_end') or '').zfill(5)
        try:
            datetime.strptime(start,'%H:%M');datetime.strptime(end,'%H:%M')
        except ValueError:raise SourceError('На raspisanie-mgsu.ru не определено время занятия')
        if not title or not re.fullmatch(r'\d{2}:\d{2}',start) or not re.fullmatch(r'\d{2}:\d{2}',end) or start>=end:
            raise SourceError('Неполное занятие raspisanie-mgsu.ru')
        prefix=KIND.match(title)
        kind=KIND_NAMES[prefix.group(1).casefold()] if prefix else ''
        if prefix:title=title[prefix.end():]
        # Preserve the site's explicit week-specific note; do not re-evaluate
        # or guess a different class from a potentially imperfect conversion.
        if row.get('kind_note'):kind+=' · '+clean(row['kind_note'])
        item=dict(date=day,start=start,end=end,title=title[:200],lesson_type=kind[:45],room=clean(row.get('location'))[:100],teacher=clean(row.get('teacher'))[:160],subgroup=sg)
        item['external_id']=stable_id('mgsu',item);out.append(item)
    return out

def get_web_schedule(date_from,date_to,today=None):
    ref=today or date.today()
    monday=ref-timedelta(days=ref.weekday())
    begin=max(date.fromisoformat(date_from),monday)
    end=min(date.fromisoformat(date_to),monday+timedelta(days=20))
    if begin>end:raise SourceError('Запрошенная дата вне резервного окна raspisanie-mgsu.ru')
    group=select_group(_json('groups'))
    out=[];day=begin
    # Limit the fallback to three weeks and one request per day. No pagination
    # guessing or unbounded scraping of the university's entire catalogue.
    while day<=end:
        out.extend(parse_day(_json('schedule',{'date':day.isoformat(),'group_id':group}),day.isoformat()))
        day+=timedelta(days=1)
    if not out:raise EmptySchedule('Резервный сайт не вернул занятий МГСУ; отсутствие пар не подтверждено')
    return ScheduleBatch(out,begin.isoformat(),end.isoformat(),label='raspisanie-mgsu.ru',provider='mgsu_web',limited=True,
                         note='Резервный публичный источник raspisanie-mgsu.ru: получены конкретные даты, а не весь семестр.',source_url=BASE)

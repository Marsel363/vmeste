from __future__ import annotations
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, date, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, func
from starlette.middleware.sessions import SessionMiddleware
from .database import Couple, User, Event, SyncState, SessionLocal, init_db, now
from .sources import get_mai, get_mgsu, get_mgsu_public, ScheduleBatch, EmptySchedule
from .mgsu_pdf import get_pdf_schedule
from .mgsu_web import get_web_schedule
from .academic_calendar import academic_calendars
from .tasks import tasks_for_user, register_task_routes
from .exclusions import scope_for_event, event_key, rules_for_couple, exclusion_keys, serialize_rule, register_exclusion_routes
from .attendance import attendance_for_couple, decorate_attendance, register_attendance_routes
from .runtime_config import allowed_origins,validate_hosted_config,allow_create_couple

ROOT=Path(__file__).parent.parent
MOSCOW=ZoneInfo('Europe/Moscow')
LOG=logging.getLogger('vmeste')
SYNC_INTERVAL=int(os.getenv('SYNC_INTERVAL_HOURS','24'))
validate_hosted_config()
allowed=allowed_origins()
secret=os.getenv('SECRET_KEY','')
if not secret or secret=='REPLACE_WITH_RANDOM_SECRET':
    # Stable only within this process; warn that sessions won't survive a restart.
    secret=secrets.token_urlsafe(48)
    LOG.warning('SECRET_KEY not configured. Sessions will be invalidated after restart.')

_connections:dict[int,set[WebSocket]]={}
_sync_lock=asyncio.Lock()

async def notify(couple_id, kind='events_changed'):
    for ws in list(_connections.get(couple_id,set())):
        try: await ws.send_json({'type':kind})
        except Exception: _connections.get(couple_id,set()).discard(ws)

def has_recent_sync(couple_id):
    with SessionLocal() as db:
        states=db.scalars(select(SyncState).where(SyncState.couple_id==couple_id)).all()
        if len(states)!=2:return False
        threshold=now()-timedelta(hours=SYNC_INTERVAL)
        return all(s.status!='unverified' and s.checked_at and aware(s.checked_at)>=threshold for s in states)

def aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

def apply_remote(couple_id, slot, incoming, provider, *, coverage=None, limited=False, sync_status=None, source_note=''):
    # Preserve all user overrides even if their source entry disappears.
    # Deactivated entries with overrides are returned with a conflict warning.
    today=datetime.now(MOSCOW).date()
    end=today+timedelta(days=62)
    start=today-timedelta(days=8)
    if coverage:
        start=max(start,date.fromisoformat(coverage[0]))
        end=min(end,date.fromisoformat(coverage[1]))
    data=[x for x in incoming if start.isoformat()<=x['date']<=end.isoformat()]
    if not data:
        raise EmptySchedule('Источник не вернул занятий в доступном периоде; отсутствие пар не подтверждено. Прежние данные сохранены')
    seen=set()
    with SessionLocal() as db:
        existing=db.scalars(select(Event).where(Event.couple_id==couple_id,Event.slot==slot,Event.source==provider,Event.date>=start.isoformat(),Event.date<=end.isoformat())).all()
        byid={e.external_id:e for e in existing}
        for x in data:
            external=x['external_id']
            seen.add(external)
            e=byid.get(external)
            if e is None:
                e=Event(couple_id=couple_id,slot=slot,source=provider,external_id=external)
                db.add(e)
                byid[external]=e
            e.date=x['date'];e.start=x['start'];e.end=x['end'];e.title=x['title'];e.room=x['room']
            e.teacher=x['teacher'];e.lesson_type=x['lesson_type'];e.subgroup=x['subgroup']
            e.active=True
        for e in existing:
            if e.external_id not in seen: e.active=False
        state=db.scalar(select(SyncState).where(SyncState.couple_id==couple_id,SyncState.slot==slot))
        if state is None:
            state=SyncState(couple_id=couple_id,slot=slot);db.add(state)
        data_dates=[date.fromisoformat(x['date']) for x in data]
        begin=min(data_dates);finish=max(data_dates)
        # Raspika public view covers one calendar week. Never label next week free.
        if provider=='raspika_web':
            begin-=timedelta(days=begin.weekday())
            finish+=timedelta(days=(6-finish.weekday()))
        if coverage:begin=start;finish=end
        state.coverage_from=begin.isoformat();state.coverage_to=finish.isoformat()
        state.count=len(data);state.successful_at=now();state.checked_at=now()
        state.status=sync_status or ('partial' if limited else 'ok')
        state.message=(f'Загружено {len(data)} занятий за {begin:%d.%m.%Y}–{finish:%d.%m.%Y}. '
                       + ('Доступна только публичная неделя; другие даты не подтверждены.' if limited else 'Данные получены из источника.'))
        if source_note:state.message=(f'Загружено {len(data)} занятий за {begin:%d.%m.%Y}–{finish:%d.%m.%Y}. '+source_note)[:300]
        db.commit()
    return len(data)

def set_sync_error(couple_id, slot, message, status='error'):
    with SessionLocal() as db:
        state=db.scalar(select(SyncState).where(SyncState.couple_id==couple_id,SyncState.slot==slot))
        if state is None: state=SyncState(couple_id=couple_id,slot=slot);db.add(state)
        state.checked_at=now();state.status=status;state.message=str(message)[:300]
        db.commit()

def _import_key(event):
    title=re.sub(r'^(?:КОП|КРП|лаб\.|пр\.|л\.)\s*','',event.title,flags=re.I)
    title=re.sub(r'(?:подгр(?:уппа)?\.?\s*[12]|-?\s*[12]\s*п\s*/\s*г)\b','',title,flags=re.I)
    return event.date,event.start,re.sub(r'\s+',' ',title).strip().casefold()

def fetch_mgsu(start,end):
    from .sources import SourceError
    mode=os.getenv('MGSU_SOURCE','auto').strip().lower()
    if mode not in ('auto','pdf','web','raspika','raspika_public'):
        raise SourceError('Неизвестный MGSU_SOURCE. Используйте auto, pdf, web или raspika')
    if mode in ('auto','pdf'):
        try:return get_pdf_schedule(start,end)
        except SourceError:
            if mode=='pdf':raise
    if mode in ('auto','web'):
        try:return get_web_schedule(start,end)
        except Exception:
            if mode=='web':raise SourceError('Не удалось получить подтверждённые даты с raspisanie-mgsu.ru; прежние данные сохранены') from None
    return get_mgsu(start,end) if mode!='raspika_public' and os.getenv('RASPIKA_API_KEY','').strip() else get_mgsu_public()

def _sync_couple(couple_id):
    results={}
    end=(datetime.now(MOSCOW).date()+timedelta(days=62)).isoformat()
    start=(datetime.now(MOSCOW).date()-timedelta(days=8)).isoformat()
    jobs=[('mai',get_mai,'mai_site')]
    jobs.append(('mgsu',lambda:fetch_mgsu(start,end),'raspika' if os.getenv('RASPIKA_API_KEY','').strip() else 'raspika_web'))
    for slot,fetch,provider in jobs:
        try:
            batch=fetch()
            if isinstance(batch,ScheduleBatch):
                data=batch.events
                provider=batch.provider or provider
                status=batch.status if batch.status!='ok' else ('partial' if batch.limited else 'ok')
                note=(batch.label+'. '+batch.note) if batch.note else ''
                count=apply_remote(couple_id,slot,data,provider,coverage=(batch.coverage_from,batch.coverage_to),limited=batch.limited,sync_status=status,source_note=note)
            else:
                data,_label=batch
                count=apply_remote(couple_id,slot,data,provider)
            # When upgrading from the public weekly page to the API, hide the old feed.
            with SessionLocal() as db:
                # A weekly fallback must not remove imported events outside that week.
                other=db.scalars(select(Event).where(Event.couple_id==couple_id,Event.slot==slot,Event.source.notin_(['manual',provider]),Event.date>=batch.coverage_from,Event.date<=batch.coverage_to)).all() if isinstance(batch,ScheduleBatch) else []
                current=db.scalars(select(Event).where(Event.couple_id==couple_id,Event.slot==slot,Event.source==provider,Event.active.is_(True))).all()
                for ev in other:
                    # Move an override only for one unambiguous occurrence of
                    # the same subject/date/start across source providers.
                    # Unmatched edits stay visible as source conflicts.
                    if json.loads(ev.override_json or '{}'):
                        key=_import_key(ev)
                        matches=[e for e in current if _import_key(e)==key]
                        edited_old=[e for e in other if _import_key(e)==key and json.loads(e.override_json or '{}')]
                        if len(matches)==1 and len(edited_old)==1 and not json.loads(matches[0].override_json or '{}'):
                            matches[0].override_json=ev.override_json
                            matches[0].revision=max(matches[0].revision,ev.revision)+1
                            ev.override_json='{}'
                    ev.active=False
                db.commit()
            results[slot]={'ok':True,'count':count,'status':status if isinstance(batch,ScheduleBatch) else 'ok'}
        except Exception as exc:
            # Avoid exposing request URLs/headers (which may contain credentials).
            from .sources import SourceError
            message=str(exc) if isinstance(exc,SourceError) else 'Источник недоступен или вернул некорректный ответ. Прежние данные сохранены; повторите проверку позже.'
            LOG.warning('%s sync failed (%s): %s',slot,type(exc).__name__,message)
            status='empty' if isinstance(exc,EmptySchedule) else 'error'
            set_sync_error(couple_id,slot,message,status)
            results[slot]={'ok':False,'status':status,'error':message[:250]}
    return results

async def sync_couple(couple_id, force=False):
    async with _sync_lock:
        if not force and has_recent_sync(couple_id):return {'skipped':True}
        result=await asyncio.to_thread(_sync_couple,couple_id)
    await notify(couple_id,'schedule_updated')
    return result

async def scheduler():
    while True:
        await asyncio.sleep(3600)
        with SessionLocal() as db:
            ids=db.scalars(select(Couple.id)).all()
        for couple_id in ids:
            if not has_recent_sync(couple_id):
                try: await sync_couple(couple_id)
                except Exception: LOG.exception('Scheduled sync failed')

@asynccontextmanager
async def lifespan(app):
    init_db()
    # v0.1 marked a single public week as a globally successful import.
    # Invalidate that assertion once, without touching events or manual edits.
    with SessionLocal() as db:
        legacy=db.scalars(select(SyncState).where(SyncState.slot=='mgsu',SyncState.status=='ok',SyncState.message.in_(['raspika','raspika_web']))).all()
        for s in legacy:
            s.status='unverified';s.message='После обновления требуется повторная проверка периода МГСУ. Сохранённые пары доступны.'
        if os.getenv('MGSU_SOURCE','auto').lower() in ('auto','pdf'):
            old_week=db.scalars(select(SyncState).where(SyncState.slot=='mgsu',SyncState.status=='partial')).all()
            for s in old_week:
                if 'Доступна только публичная неделя' in s.message:
                    s.status='unverified';s.message='Подключён семестровый PDF МГСУ. Проверяем новые даты; прежние пары сохранены.'
        db.commit()
    task=asyncio.create_task(scheduler()) if os.getenv('ENABLE_SCHEDULER','1')=='1' else None
    yield
    if task:
        task.cancel()
        try: await task
        except asyncio.CancelledError:pass

app=FastAPI(title='Вместе — два расписания',version='0.1.10',docs_url=None,redoc_url=None,lifespan=lifespan)
app.add_middleware(SessionMiddleware,secret_key=secret,same_site='lax',https_only=os.getenv('COOKIE_SECURE','0')=='1',max_age=60*60*24*30,session_cookie='vmeste_session')

@app.middleware('http')
async def protect_origin(request:Request, call_next):
    if request.url.path.startswith('/api/') and request.method in ('POST','PUT','PATCH','DELETE'):
        origin=request.headers.get('origin','').rstrip('/')
        # Browsers send Origin for cross-site POST. Reject requests from other origins.
        if origin and origin not in allowed:
            return JSONResponse({'detail':'Недопустимый источник запроса'},status_code=403)
        if request.headers.get('content-type','').split(';')[0].lower()!='application/json':
            return JSONResponse({'detail':'Требуется application/json'},status_code=415)
        # Custom header ensures same-origin JS is needed for mutation; cross-site HTML forms can't add it.
        if request.headers.get('x-vmeste-client')!='pwa':
            return JSONResponse({'detail':'Неизвестный клиент'},status_code=403)
    response=await call_next(request)
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control']='no-store'
    else:
        response.headers['X-Vmeste-App']='1'
        if request.url.path=='/sw.js':response.headers['Cache-Control']='no-cache'
    return response

def get_user(request:Request):
    uid=request.session.get('uid')
    if not uid:raise HTTPException(401,'Войдите в приложение')
    with SessionLocal() as db:
        user=db.get(User,uid)
        if not user:raise HTTPException(401,'Войдите в приложение')
        return user

def pw_hash(password, salt=None):
    salt=salt or secrets.token_hex(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),300_000).hex()
    return f'{salt}${digest}'

def pw_check(password,stored):
    try:
        salt,_=stored.split('$',1)
        return hmac.compare_digest(pw_hash(password,salt),stored)
    except ValueError:return False

class SignUp(BaseModel):
    username:str=Field(min_length=3,max_length=60,pattern=r'^[a-zA-Z0-9_.-]+$')
    display_name:str=Field(min_length=1,max_length=45)
    password:str=Field(min_length=10,max_length=150)
    mode:str=Field(pattern=r'^(create|join)$')
    invite_code:str|None=None

class SignIn(BaseModel):
    username:str
    password:str

@app.get('/api/me')
def me(request:Request):
    uid=request.session.get('uid')
    if not uid:return {'authenticated':False}
    with SessionLocal() as db:
        u=db.get(User,uid)
        if not u:return {'authenticated':False}
        couple=db.get(Couple,u.couple_id)
        users=db.scalars(select(User).where(User.couple_id==u.couple_id).order_by(User.slot)).all()
        return {'authenticated':True,'user':{'id':u.id,'username':u.username,'name':u.display_name,'slot':u.slot},
                'members':[{'name':x.display_name,'slot':x.slot} for x in users],
                'invite_code':couple.code if len(users)==1 else None}

@app.post('/api/register')
async def register(payload:SignUp,request:Request):
    username=payload.username.lower()
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.username==username)):
            raise HTTPException(409,'Такой логин уже используется')
        if payload.mode=='create':
            if not allow_create_couple():
                raise HTTPException(403,'Календарь ещё не перенесён на сервер. Войдите после переноса существующих аккаунтов')
            if db.scalar(select(Couple.id)) is not None:
                raise HTTPException(403,'Этот календарь уже создан. Воспользуйтесь кодом приглашения')
            couple=Couple();db.add(couple);db.flush();slot='mai'
        else:
            couple=db.scalar(select(Couple).where(Couple.code==str(payload.invite_code or '').strip()))
            if not couple:raise HTTPException(404,'Код приглашения не найден')
            if db.scalar(select(User.id).where(User.couple_id==couple.id,User.slot=='mgsu')):
                raise HTTPException(409,'К паре уже подключены два человека')
            slot='mgsu'
        u=User(couple_id=couple.id,slot=slot,username=username,display_name=payload.display_name.strip(),password_hash=pw_hash(payload.password))
        db.add(u);db.commit();request.session['uid']=u.id
        cid=couple.id
    # Sync only in background; registration response is fast.
    asyncio.create_task(sync_couple(cid))
    return {'ok':True}

@app.post('/api/login')
def login(payload:SignIn,request:Request):
    with SessionLocal() as db:
        u=db.scalar(select(User).where(User.username==payload.username.lower()))
        if u is None or not pw_check(payload.password,u.password_hash):raise HTTPException(401,'Неверный логин или пароль')
        request.session.clear();request.session['uid']=u.id
        return {'ok':True}

@app.post('/api/logout')
def logout(request:Request):
    request.session.clear();return {'ok':True}

def serialize_event(e):
    override=json.loads(e.override_json or '{}')
    raw={k:getattr(e,k) for k in ('id','slot','date','start','end','title','lesson_type','teacher','room','source','revision')}
    raw.update({k:v for k,v in override.items() if k in ('date','start','end','title','lesson_type','teacher','room')})
    raw['cancelled']=bool(override.get('cancelled',False))
    raw['conflict']=not e.active and e.source!='manual'
    raw['manually_edited']=bool(override)
    raw['subgroup']=e.subgroup
    raw['note']=str(override.get('note') or '')
    raw['exclusion_scope']=scope_for_event(e)
    return raw

@app.get('/api/schedule')
async def get_schedule(request:Request,date_from:str|None=None,date_to:str|None=None):
    u=get_user(request)
    if not has_recent_sync(u.couple_id) and not _sync_lock.locked():
        asyncio.create_task(sync_couple(u.couple_id))
    today=datetime.now(MOSCOW).date()
    a=date_from or (today-timedelta(days=7)).isoformat()
    b=date_to or (today+timedelta(days=35)).isoformat()
    if not re.fullmatch(r'20\d\d-\d\d-\d\d',a) or not re.fullmatch(r'20\d\d-\d\d-\d\d',b):raise HTTPException(422,'Неверный диапазон дат')
    try: span=(date.fromisoformat(b)-date.fromisoformat(a)).days
    except ValueError:raise HTTPException(422,'Неверная дата') from None
    if span not in range(0,185):raise HTTPException(422,'Диапазон не более 184 дней')
    with SessionLocal() as db:
        # An override can move an imported event to another day: fetch all scoped events, filter on effective date.
        events=db.scalars(select(Event).where(Event.couple_id==u.couple_id)).all()
        exclusions=rules_for_couple(db,u.couple_id)
        excluded=exclusion_keys(exclusions)
        owners,lesson_marks,day_marks,public_days=attendance_for_couple(db,u,a,b)
        displayed=[decorate_attendance(e,serialize_event(e),u,owners,lesson_marks,day_marks) for e in events if (e.active or json.loads(e.override_json or '{}')) and event_key(e) not in excluded]
        displayed=[e for e in displayed if a<=e['date']<=b]
        displayed.sort(key=lambda x:(x['date'],x['start'],x['slot'],x['id']))
        states=db.scalars(select(SyncState).where(SyncState.couple_id==u.couple_id)).all()
        state_by_slot={s.slot:{'status':s.status,'message':s.message,'count':s.count,
                                'reliable':bool(s.status in ('ok','partial') and s.count>0 and s.successful_at and aware(s.successful_at)>=now()-timedelta(hours=SYNC_INTERVAL)),
                                # A saved semester grid is usable even when a fresh check failed.
                                'loaded':bool(s.successful_at and s.count>0 and s.coverage_from and s.coverage_to and s.coverage_from<=s.coverage_to and s.status not in ('never','unverified')),
                                'checked_at':aware(s.checked_at).isoformat() if s.checked_at else None,
                                'successful_at':aware(s.successful_at).isoformat() if s.successful_at else None,
                                'coverage_from':s.coverage_from,'coverage_to':s.coverage_to} for s in states}
        return {'events':displayed,'tasks':tasks_for_user(db,u,a,b),'tasks_complete':True,
                'tasks_coverage':{'from':a,'to':b},'sources':state_by_slot,
                'attendance_days':public_days,'attendance_complete':True,'attendance_coverage':{'from':a,'to':b},
                'exclusions':[serialize_rule(rule) for rule in exclusions],'exclusions_complete':True,
                'academic_calendars':academic_calendars(),'server_now':datetime.now(MOSCOW).isoformat()}

register_task_routes(app,get_user,notify)
register_exclusion_routes(app,get_user,notify)
register_attendance_routes(app,get_user,notify)

class EventPayload(BaseModel):
    slot:str=Field(pattern=r'^(mai|mgsu)$')
    date:str
    start:str
    end:str
    title:str=Field(min_length=1,max_length=200)
    lesson_type:str=Field(default='',max_length=45)
    room:str=Field(default='',max_length=100)
    teacher:str=Field(default='',max_length=160)
    note:str=Field(default='',max_length=1000)
    @model_validator(mode='after')
    def validate_fields(self):
        try:date.fromisoformat(self.date)
        except ValueError:raise ValueError('Неверная дата')
        if not re.fullmatch(r'\d{2}:\d{2}',self.start) or not re.fullmatch(r'\d{2}:\d{2}',self.end) or self.start>=self.end or self.end>'23:59':
            raise ValueError('Укажите корректное время начала и окончания')
        return self

@app.post('/api/events')
async def create_event(payload:EventPayload,request:Request):
    u=get_user(request)
    with SessionLocal() as db:
        e=Event(couple_id=u.couple_id,slot=payload.slot,date=payload.date,start=payload.start,end=payload.end,title=payload.title.strip(),
                lesson_type=payload.lesson_type,room=payload.room,teacher=payload.teacher,source='manual',active=True,
                override_json=json.dumps({'note':payload.note},ensure_ascii=False) if payload.note else '{}')
        db.add(e);db.commit();db.refresh(e)
        result=serialize_event(e)
    await notify(u.couple_id)
    return result

class EditPayload(BaseModel):
    revision:int
    cancelled:bool=False
    date:str
    start:str
    end:str
    title:str=Field(min_length=1,max_length=200)
    lesson_type:str=Field(default='',max_length=45)
    room:str=Field(default='',max_length=100)
    teacher:str=Field(default='',max_length=160)
    # Omission by an older client preserves the existing note; '' clears it.
    note:str|None=Field(default=None,max_length=1000)
    @model_validator(mode='after')
    def validate_fields(self):
        EventPayload(slot='mai',date=self.date,start=self.start,end=self.end,title=self.title,
                     lesson_type=self.lesson_type,teacher=self.teacher,room=self.room)
        return self

@app.put('/api/events/{event_id}')
async def update_event(event_id:int,payload:EditPayload,request:Request):
    u=get_user(request)
    with SessionLocal() as db:
        e=db.get(Event,event_id)
        if e is None or e.couple_id!=u.couple_id:raise HTTPException(404,'Пара не найдена')
        if e.revision!=payload.revision:raise HTTPException(409,'Эта пара уже изменилась. Обновите страницу и повторите')
        fields=payload.model_dump(exclude={'revision','note'})
        old_override=json.loads(e.override_json or '{}')
        note=payload.note if payload.note is not None else str(old_override.get('note') or '')
        if e.source=='manual':
            for k,v in fields.items():
                if k!='cancelled':setattr(e,k,v)
            e.override_json=json.dumps({'cancelled':True}) if fields['cancelled'] else '{}'
        else:
            base={k:getattr(e,k) for k in fields if k!='cancelled'}
            delta={k:v for k,v in fields.items() if k!='cancelled' and v!=base[k]}
            if fields['cancelled']:delta['cancelled']=True
            e.override_json=json.dumps(delta,ensure_ascii=False)
        override=json.loads(e.override_json or '{}')
        if note:override['note']=note
        e.override_json=json.dumps(override,ensure_ascii=False)
        e.revision+=1
        db.commit();result=serialize_event(e)
    await notify(u.couple_id)
    return result

@app.delete('/api/events/{event_id}')
async def delete_event(event_id:int,request:Request):
    u=get_user(request)
    with SessionLocal() as db:
        e=db.get(Event,event_id)
        if e is None or e.couple_id!=u.couple_id:raise HTTPException(404,'Пара не найдена')
        if e.source!='manual':raise HTTPException(400,'Импортированную пару можно отменить, но не удалить')
        db.delete(e);db.commit()
    await notify(u.couple_id)
    return {'ok':True}

@app.post('/api/sync')
async def sync_now(request:Request):
    u=get_user(request)
    return await sync_couple(u.couple_id,force=True)

@app.websocket('/api/live')
async def live(websocket:WebSocket):
    origin=websocket.headers.get('origin','').rstrip('/')
    if origin and origin not in allowed:
        await websocket.close(code=1008);return
    uid=websocket.session.get('uid')
    if not uid:
        await websocket.close(code=1008);return
    with SessionLocal() as db:
        u=db.get(User,uid)
        if not u:
            await websocket.close(code=1008);return
        cid=u.couple_id
    await websocket.accept()
    _connections.setdefault(cid,set()).add(websocket)
    try:
        while True:await websocket.receive_text()
    except WebSocketDisconnect:pass
    finally:_connections.get(cid,set()).discard(websocket)

@app.get('/')
async def index():return FileResponse(ROOT/'static'/'index.html')

app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')

@app.get('/manifest.webmanifest')
def manifest():return FileResponse(ROOT/'static'/'manifest.webmanifest',media_type='application/manifest+json')

@app.get('/sw.js')
def service_worker():return FileResponse(ROOT/'static'/'sw.js',media_type='application/javascript')

@app.get('/healthz')
def health():return {'ok':True}


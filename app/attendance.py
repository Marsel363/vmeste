"""Personal attendance, visible to the couple; writes always derive the owner from session."""
import hashlib
import hmac
import json
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from .database import Event, User, DayAttendance, LessonAttendance, SessionLocal, now
from .exclusions import event_key
from .tasks import valid_day


def lesson_token(event):
    # One source occurrence, stable across provider IDs, notes, room and one-off edits.
    if event.source=='manual':
        return hashlib.sha256(f'manual:{event.couple_id}:{event.id}'.encode()).hexdigest()
    return hashlib.sha256(json.dumps([event.date,*event_key(event)],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def attendance_for_couple(db,viewer,begin,end):
    members=db.scalars(select(User).where(User.couple_id==viewer.couple_id)).all()
    owners={u.slot:u for u in members}
    lessons={(r.owner_id,r.source_key):r.not_going for r in db.scalars(select(LessonAttendance).where(
        LessonAttendance.couple_id==viewer.couple_id)).all()}
    days=db.scalars(select(DayAttendance).where(DayAttendance.couple_id==viewer.couple_id,
        DayAttendance.date>=begin,DayAttendance.date<=end,DayAttendance.not_going.is_(True))).all()
    by_id={u.id:u for u in members}
    public=[{'owner_id':r.owner_id,'slot':by_id[r.owner_id].slot,'date':r.date,'not_going':True}
            for r in days if r.owner_id in by_id]
    return owners,lessons,{(r.owner_id,r.date) for r in days},public


def decorate_attendance(event,raw,viewer,owners,lessons,days):
    owner=owners.get(event.slot)
    token=lesson_token(event)
    single=bool(owner and lessons.get((owner.id,token),False))
    whole_day=bool(owner and (owner.id,raw['date']) in days)
    return {**raw,'attendance_scope':token,'can_mark_attendance':bool(owner and owner.id==viewer.id),
            'lesson_not_going':single,'day_not_going':whole_day,'not_going':single or whole_day}


class AttendancePayload(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    not_going:bool


class LessonAttendancePayload(AttendancePayload):
    revision:int=Field(ge=1)
    scope_token:str=Field(pattern=r'^[0-9a-f]{64}$')


def save_mark(db,model,values,keys):
    # Atomic upsert prevents duplicate rows/races on retries and two devices.
    insert=postgres_insert if db.bind.dialect.name=='postgresql' else sqlite_insert
    stmt=insert(model).values(**values)
    db.execute(stmt.on_conflict_do_update(index_elements=keys,set_={'not_going':values['not_going'],'updated_at':now()}))
    db.commit()


def register_attendance_routes(app,get_user,notify):
    @app.put('/api/events/{event_id}/attendance')
    async def mark_lesson(event_id:int,payload:LessonAttendancePayload,request:Request):
        viewer=get_user(request)
        with SessionLocal() as db:
            event=db.get(Event,event_id)
            if event is None or event.couple_id!=viewer.couple_id:raise HTTPException(404,'Пара не найдена')
            if event.slot!=viewer.slot:raise HTTPException(403,'Можно отмечать посещение только своих пар')
            token=lesson_token(event)
            if event.revision!=payload.revision or not hmac.compare_digest(token,payload.scope_token):
                raise HTTPException(409,'Пара изменилась. Откройте её заново')
            save_mark(db,LessonAttendance,dict(couple_id=viewer.couple_id,owner_id=viewer.id,
                source_key=token,not_going=payload.not_going),['owner_id','source_key'])
        await notify(viewer.couple_id,'attendance_changed')
        return {'scope_token':token,'not_going':payload.not_going}

    @app.put('/api/attendance/days/{day}')
    async def mark_day(day:str,payload:AttendancePayload,request:Request):
        viewer=get_user(request)
        try:valid_day(day)
        except ValueError:raise HTTPException(422,'Укажите корректную дату') from None
        with SessionLocal() as db:
            save_mark(db,DayAttendance,dict(couple_id=viewer.couple_id,owner_id=viewer.id,
                date=day,not_going=payload.not_going),['owner_id','date'])
        await notify(viewer.couple_id,'attendance_changed')
        return {'owner_id':viewer.id,'slot':viewer.slot,'date':day,'not_going':payload.not_going}

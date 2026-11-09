"""Reversible exclusions of one subject's weekly time slot, scoped to a couple."""
from datetime import date
import hashlib
import hmac
import json
import re
import unicodedata
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .database import Event, LessonExclusion, SessionLocal


def subject_key(title):
    title=unicodedata.normalize('NFKC',title).casefold().replace('ё','е')
    title=re.sub(r'^(?:коп|крп|лаб\.?|пр\.?|л\.)\s+','',title)
    title=re.sub('[–—−]','-',title)
    # Keep meaningful punctuation: C++ and C# are different subjects.
    return re.sub(r'\s+',' ',title).strip(' .;:')


def event_key(event):
    # Stable source identity, independent of single-date user overrides and provider IDs.
    return event.slot,subject_key(event.title),date.fromisoformat(event.date).weekday(),event.start,event.end


def scope_for_event(event):
    slot,key,weekday,start,end=event_key(event)
    token=hashlib.sha256(json.dumps([slot,key,weekday,start,end],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    return {'title':event.title,'slot':slot,'weekday':weekday,'start':start,'end':end,'token':token}


def rules_for_couple(db,couple_id):
    return db.scalars(select(LessonExclusion).where(LessonExclusion.couple_id==couple_id).order_by(
        LessonExclusion.slot,LessonExclusion.weekday,LessonExclusion.start,LessonExclusion.id)).all()


def exclusion_keys(rules):
    return {(r.slot,r.title_key,r.weekday,r.start,r.end) for r in rules}


def serialize_rule(rule):
    return {k:getattr(rule,k) for k in ('id','slot','title','weekday','start','end')}


class ExclusionPayload(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(ge=1)
    scope_token:str=Field(pattern=r'^[0-9a-f]{64}$')


def register_exclusion_routes(app,get_user,notify):
    @app.post('/api/events/{event_id}/exclude-series')
    async def exclude_series(event_id:int,payload:ExclusionPayload,request:Request):
        viewer=get_user(request)
        with SessionLocal() as db:
            event=db.get(Event,event_id)
            if event is None or event.couple_id!=viewer.couple_id:
                raise HTTPException(404,'Пара не найдена')
            scope=scope_for_event(event)
            if event.revision!=payload.revision or not hmac.compare_digest(scope['token'],payload.scope_token):
                raise HTTPException(409,'Пара изменилась. Откройте её заново перед исключением')
            filters=[LessonExclusion.couple_id==viewer.couple_id,LessonExclusion.slot==event.slot,
                     LessonExclusion.title_key==subject_key(event.title),LessonExclusion.weekday==scope['weekday'],
                     LessonExclusion.start==event.start,LessonExclusion.end==event.end]
            rule=db.scalar(select(LessonExclusion).where(*filters))
            if rule is None:
                rule=LessonExclusion(couple_id=viewer.couple_id,created_by=viewer.id,slot=event.slot,
                    title_key=subject_key(event.title),title=event.title,weekday=scope['weekday'],start=event.start,end=event.end)
                db.add(rule)
                try:db.commit()
                except IntegrityError:
                    db.rollback()
                    rule=db.scalar(select(LessonExclusion).where(*filters))
                    if rule is None:raise
            result=serialize_rule(rule)
        await notify(viewer.couple_id,'exclusions_changed')
        return result

    @app.delete('/api/lesson-exclusions/{rule_id}')
    async def restore_series(rule_id:int,request:Request):
        viewer=get_user(request)
        with SessionLocal() as db:
            rule=db.scalar(select(LessonExclusion).where(LessonExclusion.id==rule_id,LessonExclusion.couple_id==viewer.couple_id))
            if rule is None:raise HTTPException(404,'Исключение не найдено')
            db.delete(rule);db.commit()
        await notify(viewer.couple_id,'exclusions_changed')
        return {'ok':True}


"""Personal plans: shared visibility within a couple, ownership on every write."""
from datetime import date
import re
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update, delete
from .database import PersonalTask, User, SessionLocal, now


def valid_day(value):
    if not re.fullmatch(r'20\d\d-\d\d-\d\d', value):
        raise ValueError('Укажите корректную дату')
    date.fromisoformat(value)
    return value


class TaskPayload(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    date: str
    note: str = Field(default='', max_length=1000)
    start: str | None = None
    end: str | None = None
    busy: bool = False
    done: bool = False

    @model_validator(mode='after')
    def validate_task(self):
        valid_day(self.date)
        self.start = self.start or None
        self.end = self.end or None
        if bool(self.start) != bool(self.end):
            raise ValueError('Укажите и начало, и конец дела')
        if self.start:
            pattern = r'(?:[01]\d|2[0-3]):[0-5]\d'
            if not re.fullmatch(pattern, self.start) or not re.fullmatch(pattern, self.end) or self.start >= self.end:
                raise ValueError('Окончание должно быть позже начала, в пределах одного дня')
        if self.busy and not self.start:
            raise ValueError('Для занятости нужно указать время')
        return self


class EditTaskPayload(TaskPayload):
    revision: int = Field(ge=1)


class TaskRevision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)


def serialize_task(task, owner, viewer):
    return {**{k: getattr(task, k) for k in ('id', 'owner_id', 'date', 'title', 'note', 'start', 'end', 'busy', 'done', 'revision')},
            'slot': owner.slot, 'owner_name': owner.display_name, 'can_edit': task.owner_id == viewer.id}


def tasks_for_user(db, viewer, begin, end):
    rows = db.execute(select(PersonalTask, User).join(User, PersonalTask.owner_id == User.id).where(
        PersonalTask.couple_id == viewer.couple_id, User.couple_id == viewer.couple_id,
        PersonalTask.date >= begin, PersonalTask.date <= end).order_by(
        PersonalTask.date, PersonalTask.done, PersonalTask.start, PersonalTask.id)).all()
    return [serialize_task(task, owner, viewer) for task, owner in rows]


def owned_task(db, task_id, viewer):
    task = db.scalar(select(PersonalTask).where(PersonalTask.id == task_id, PersonalTask.couple_id == viewer.couple_id))
    if task is None:
        raise HTTPException(404, 'Дело не найдено')
    if task.owner_id != viewer.id:
        raise HTTPException(403, 'Можно изменять только свои дела')
    return task


def register_task_routes(app, get_user, notify):
    @app.post('/api/tasks')
    async def create_task(payload: TaskPayload, request: Request):
        viewer = get_user(request)
        with SessionLocal() as db:
            task = PersonalTask(couple_id=viewer.couple_id, owner_id=viewer.id, **payload.model_dump())
            db.add(task); db.commit(); db.refresh(task)
            result = serialize_task(task, viewer, viewer)
        await notify(viewer.couple_id, 'tasks_changed')
        return result

    @app.put('/api/tasks/{task_id}')
    async def edit_task(task_id: int, payload: EditTaskPayload, request: Request):
        viewer = get_user(request)
        with SessionLocal() as db:
            owned_task(db, task_id, viewer)
            changed = db.execute(update(PersonalTask).where(
                PersonalTask.id == task_id, PersonalTask.owner_id == viewer.id,
                PersonalTask.couple_id == viewer.couple_id, PersonalTask.revision == payload.revision).values(
                **payload.model_dump(exclude={'revision'}), revision=PersonalTask.revision + 1, updated_at=now()))
            if changed.rowcount != 1:
                raise HTTPException(409, 'Дело уже изменилось. Обновите список и повторите')
            db.commit()
            task = db.get(PersonalTask, task_id)
            result = serialize_task(task, viewer, viewer)
        await notify(viewer.couple_id, 'tasks_changed')
        return result

    @app.delete('/api/tasks/{task_id}')
    async def delete_task(task_id: int, payload: TaskRevision, request: Request):
        viewer = get_user(request)
        with SessionLocal() as db:
            owned_task(db, task_id, viewer)
            changed = db.execute(delete(PersonalTask).where(
                PersonalTask.id == task_id, PersonalTask.owner_id == viewer.id,
                PersonalTask.couple_id == viewer.couple_id, PersonalTask.revision == payload.revision))
            if changed.rowcount != 1:
                raise HTTPException(409, 'Дело уже изменилось. Обновите список и повторите')
            db.commit()
        await notify(viewer.couple_id, 'tasks_changed')
        return {'ok': True}


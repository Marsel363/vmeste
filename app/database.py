import os
import secrets
from datetime import datetime, timezone
from sqlalchemy import create_engine, Integer, String, DateTime, ForeignKey, Boolean, Text, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

URL = os.getenv('DATABASE_URL', 'sqlite:///./vmeste.db')
if URL.startswith('postgres://'):
    URL = URL.replace('postgres://', 'postgresql+psycopg://', 1)
if URL.startswith('postgresql://'):
    URL = URL.replace('postgresql://', 'postgresql+psycopg://', 1)
engine = create_engine(URL, connect_args={'check_same_thread': False} if URL.startswith('sqlite') else {}, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

def now():
    return datetime.now(timezone.utc)

class Couple(Base):
    __tablename__ = 'couples'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(24), unique=True, default=lambda: secrets.token_urlsafe(10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    couple_id: Mapped[int] = mapped_column(ForeignKey('couples.id'), index=True)
    username: Mapped[str] = mapped_column(String(60), unique=True)
    display_name: Mapped[str] = mapped_column(String(45))
    password_hash: Mapped[str] = mapped_column(String(256))
    slot: Mapped[str] = mapped_column(String(6))  # mai/mgsu
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('couple_id','slot', name='uq_couple_slot'),)

class Event(Base):
    __tablename__ = 'events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    couple_id: Mapped[int] = mapped_column(ForeignKey('couples.id'), index=True)
    slot: Mapped[str] = mapped_column(String(6), index=True)
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(24), default='manual')
    date: Mapped[str] = mapped_column(String(10), index=True)
    start: Mapped[str] = mapped_column(String(5))
    end: Mapped[str] = mapped_column(String(5))
    title: Mapped[str] = mapped_column(String(200))
    lesson_type: Mapped[str] = mapped_column(String(45), default='')
    teacher: Mapped[str] = mapped_column(String(160), default='')
    room: Mapped[str] = mapped_column(String(100), default='')
    subgroup: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # User-layer edits stay separate from imported source fields.
    override_json: Mapped[str] = mapped_column(Text, default='{}')
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (UniqueConstraint('couple_id', 'slot', 'source', 'external_id', name='uq_imported_event'),)

class SyncState(Base):
    __tablename__ = 'sync_states'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    couple_id: Mapped[int] = mapped_column(ForeignKey('couples.id'))
    slot: Mapped[str] = mapped_column(String(6))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    coverage_from: Mapped[str | None] = mapped_column(String(10), nullable=True)
    coverage_to: Mapped[str | None] = mapped_column(String(10), nullable=True)
    successful_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(15), default='never')
    message: Mapped[str] = mapped_column(String(300), default='')
    count: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint('couple_id', 'slot', name='uq_sync_slot'),)

class PersonalTask(Base):
    __tablename__ = 'personal_tasks'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    couple_id: Mapped[int] = mapped_column(ForeignKey('couples.id'), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)
    title: Mapped[str] = mapped_column(String(200))
    note: Mapped[str] = mapped_column(Text, default='')
    start: Mapped[str | None] = mapped_column(String(5), nullable=True)
    end: Mapped[str | None] = mapped_column(String(5), nullable=True)
    busy: Mapped[bool] = mapped_column(Boolean, default=False)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (
        CheckConstraint('(start IS NULL AND "end" IS NULL) OR (start IS NOT NULL AND "end" IS NOT NULL AND start < "end")', name='ck_task_time_pair'),
        CheckConstraint('(NOT busy) OR (start IS NOT NULL AND "end" IS NOT NULL)', name='ck_task_busy_time'),
    )

def init_db():
    Base.metadata.create_all(engine)


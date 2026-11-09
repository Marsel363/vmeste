import json
from pathlib import Path
import pytest
from sqlalchemy import create_engine,select
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from app import main
from app.runtime_config import validate_hosted_config,allowed_origins
from app.database import Base,Couple,User,Event,SyncState,PersonalTask,LessonExclusion,DayAttendance,LessonAttendance
from app.transfer_database import copy_calendar
import sqlite3

def memory_engine():return create_engine('sqlite://',poolclass=StaticPool)

def test_transfer_preserves_accounts_notes_and_stable_ids():
    source=memory_engine();target=memory_engine();Base.metadata.create_all(source)
    try:
        with source.begin() as db:
            db.execute(Couple.__table__.insert(),{'id':7,'code':'existing-invite'})
            db.execute(User.__table__.insert(),{'id':12,'couple_id':7,'username':'existing-user','display_name':'Имя','password_hash':'keep-exact-hash','slot':'mai'})
            db.execute(Event.__table__.insert(),{'id':30,'couple_id':7,'slot':'mai','date':'2026-11-09','start':'10:00','end':'11:20','title':'Предмет','override_json':'{"note":"Контрольная"}'})
        counts=copy_calendar(source,target)
        assert counts['users']==1 and counts['events']==1
        with target.connect() as db:
            user=db.execute(select(User.__table__)).mappings().one()
            assert user['id']==12 and user['password_hash']=='keep-exact-hash'
            assert json.loads(db.execute(select(Event.override_json)).scalar_one())['note']=='Контрольная'
        with pytest.raises(ValueError,match='уже содержит данные'):copy_calendar(source,target)
        with target.connect() as db:assert len(db.execute(select(User.id)).all())==1
    finally:source.dispose();target.dispose()

def test_transfer_refuses_empty_source():
    source=memory_engine();target=memory_engine();Base.metadata.create_all(source)
    try:
        with pytest.raises(ValueError,match='нет календаря'):copy_calendar(source,target)
    finally:source.dispose();target.dispose()

def test_transfer_of_previous_version_without_tasks_table():
    source=memory_engine();target=memory_engine();Base.metadata.create_all(source)
    PersonalTask.__table__.drop(source)
    with source.begin() as db:
        db.execute(Couple.__table__.insert(),{'id':1,'code':'keep'})
        db.execute(User.__table__.insert(),{'id':1,'couple_id':1,'username':'existing','display_name':'Имя','password_hash':'keep','slot':'mai'})
    try:
        counts=copy_calendar(source,target)
        assert counts['users']==1 and counts['personal_tasks']==0
        from sqlalchemy import inspect
        assert not inspect(source).has_table('personal_tasks')
    finally:source.dispose();target.dispose()

def test_transfer_of_previous_version_without_exclusions_table():
    source=memory_engine();target=memory_engine();Base.metadata.create_all(source)
    LessonExclusion.__table__.drop(source)
    with source.begin() as db:
        db.execute(Couple.__table__.insert(),{'id':1,'code':'keep'})
        db.execute(User.__table__.insert(),{'id':1,'couple_id':1,'username':'existing','display_name':'Имя','password_hash':'keep','slot':'mai'})
        db.execute(PersonalTask.__table__.insert(),{'id':2,'couple_id':1,'owner_id':1,'date':'2026-11-09','title':'Сохранить дело'})
    try:
        counts=copy_calendar(source,target)
        assert counts['personal_tasks']==1 and counts['lesson_exclusions']==0
        from sqlalchemy import inspect
        assert not inspect(source).has_table('lesson_exclusions')
    finally:source.dispose();target.dispose()

def test_windows_file_source_is_read_only_and_not_modified(tmp_path):
    file=tmp_path/'calendar.db';setup=create_engine('sqlite:///'+str(file));Base.metadata.create_all(setup)
    with setup.begin() as db:
        db.execute(Couple.__table__.insert(),{'id':1,'code':'keep-code'})
        db.execute(User.__table__.insert(),{'id':1,'couple_id':1,'username':'existing','display_name':'Имя','password_hash':'keep-hash','slot':'mai'})
    setup.dispose();before=file.read_bytes()
    source=create_engine('sqlite://',creator=lambda:sqlite3.connect(file.as_uri()+'?mode=ro',uri=True));target=memory_engine()
    try:
        assert copy_calendar(source,target)['users']==1
        assert file.read_bytes()==before
    finally:source.dispose();target.dispose()

def test_transfer_of_v017_without_attendance_tables():
    source=memory_engine();target=memory_engine();Base.metadata.create_all(source)
    DayAttendance.__table__.drop(source);LessonAttendance.__table__.drop(source)
    with source.begin() as db:
        db.execute(Couple.__table__.insert(),{'id':1,'code':'keep'})
        db.execute(User.__table__.insert(),{'id':1,'couple_id':1,'username':'existing','display_name':'Имя','password_hash':'keep','slot':'mai'})
        db.execute(PersonalTask.__table__.insert(),{'id':2,'couple_id':1,'owner_id':1,'date':'2026-11-09','title':'Сохранить дело'})
        db.execute(LessonExclusion.__table__.insert(),{'id':3,'couple_id':1,'created_by':1,'slot':'mai','title_key':'физика','title':'Физика','weekday':0,'start':'10:00','end':'11:00'})
    try:
        counts=copy_calendar(source,target)
        assert counts['personal_tasks']==1 and counts['lesson_exclusions']==1
        assert counts['day_attendance']==counts['lesson_attendance']==0
        from sqlalchemy import inspect
        assert not inspect(source).has_table('day_attendance') and not inspect(source).has_table('lesson_attendance')
    finally:source.dispose();target.dispose()

def test_hosted_config_refuses_temporary_sqlite_and_missing_secret(monkeypatch):
    monkeypatch.setenv('APP_ENV','production');monkeypatch.setenv('SECRET_KEY','x'*40);monkeypatch.setenv('COOKIE_SECURE','1')
    monkeypatch.setenv('DATABASE_URL','sqlite:///vmeste.db')
    with pytest.raises(RuntimeError,match='PostgreSQL'):validate_hosted_config()
    monkeypatch.setenv('DATABASE_URL','postgresql://example/db')
    monkeypatch.setenv('SECRET_KEY','')
    with pytest.raises(RuntimeError,match='SECRET_KEY'):validate_hosted_config()
    monkeypatch.setenv('SECRET_KEY','x'*40);validate_hosted_config()

def test_render_origin_is_added_without_wildcard(monkeypatch):
    monkeypatch.setenv('RENDER_EXTERNAL_URL','https://vmeste-example.onrender.com')
    assert 'https://vmeste-example.onrender.com' in allowed_origins()
    assert '*' not in allowed_origins()

def test_static_and_private_response_cache_headers(monkeypatch):
    async def no_sync(*a,**k):return {}
    monkeypatch.setattr(main,'sync_couple',no_sync)
    with TestClient(main.app) as client:
        assert client.get('/').headers['x-vmeste-app']=='1'
        assert client.get('/sw.js').headers['cache-control']=='no-cache'
        assert client.get('/api/me').headers['cache-control']=='no-store'

def test_cloud_build_contains_pdf_and_only_free_service():
    root=Path(__file__).resolve().parent.parent
    assert 'COPY data ./data' in (root/'Dockerfile').read_text()
    blueprint=(root/'render.yaml').read_text()
    assert 'plan: free' in blueprint and 'generateValue: true' in blueprint
    assert 'databases:' not in blueprint  # no expiring Render Postgres trial

def test_public_service_cannot_be_claimed_before_transfer(monkeypatch):
    monkeypatch.setenv('ALLOW_CREATE_COUPLE','0')
    with TestClient(main.app) as client:
        response=client.post('/api/register',json={'username':'stranger','display_name':'Имя','password':'very-secure-pass','mode':'create'},
                             headers={'Origin':'http://localhost:8000','X-Vmeste-Client':'pwa'})
        assert response.status_code==403 and 'перенесён' in response.json()['detail']


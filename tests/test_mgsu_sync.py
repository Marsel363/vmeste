"""Regression tests for limited weeks, failures and preservation of user data."""
import asyncio
import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock
import pytest
from sqlalchemy import select
from app import sources, main
from app.database import Base, engine, SessionLocal, Couple, Event, SyncState


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    class FixedDatetime(datetime):
        @classmethod
        def now(cls,tz=None):
            return datetime(2026,11,7,12,tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(main,'datetime',FixedDatetime)
    monkeypatch.delenv('RASPIKA_API_KEY',raising=False)
    with SessionLocal() as db:
        db.add(Couple(id=1));db.commit()


def lesson(day='2026-11-03', title='Проверочное занятие'):
    row=dict(date=day,start='10:00',end='11:20',title=title,room='124/КМК',teacher='',lesson_type='Практика',subgroup=1)
    row['external_id']=sources.stable_id('mgsu',row)
    return row


def public_batch():
    html=(Path(__file__).parent/'fixtures/mgsu_public_2026-11-07.html').read_text(encoding='utf-8')
    return sources.parse_raspika_batch(html,today=date(2026,11,7))


def test_real_public_response_is_one_week():
    batch=public_batch()
    assert batch.coverage_from=='2026-11-02'
    assert batch.coverage_to=='2026-11-08'
    assert len(batch.events)==14
    assert all('2026-11-02'<=e['date']<='2026-11-06' for e in batch.events)
    assert batch.events[0]['title']=='Высшая математика'
    assert batch.events[0]['room']=='107/КПА'
    assert batch.events[-1]['room']=='СОК'
    assert all(e['subgroup'] in (0,1) for e in batch.events)


def test_sync_exposes_limited_status(monkeypatch):
    monkeypatch.setattr(main,'get_mai',lambda:([lesson('2026-11-09')],'МАИ'))
    monkeypatch.setattr(main,'get_mgsu_public',public_batch)
    results=main._sync_couple(1)
    assert results['mgsu']==dict(ok=True,count=14,status='partial')
    with SessionLocal() as db:
        s=db.scalar(select(SyncState).where(SyncState.slot=='mgsu'))
        assert s.status=='partial' and s.coverage_to=='2026-11-08'
        assert 'публичная неделя' in s.message
        assert s.coverage_to<'2026-11-09'


def test_empty_import_keeps_events_edits_and_last_success(monkeypatch):
    main.apply_remote(1,'mgsu',[lesson()],'raspika_web',coverage=('2026-11-02','2026-11-08'),limited=True)
    with SessionLocal() as db:
        e=db.scalar(select(Event));e.override_json=json.dumps({'room':'Моя аудитория','cancelled':True})
        s=db.scalar(select(SyncState));last_success=s.successful_at;db.commit()
    monkeypatch.setattr(main,'get_mai',Mock(side_effect=sources.SourceError('МАИ недоступен')))
    monkeypatch.setattr(main,'get_mgsu_public',lambda:sources.ScheduleBatch([],'2026-11-02','2026-11-08',limited=True))
    assert main._sync_couple(1)['mgsu']['ok'] is False
    with SessionLocal() as db:
        e=db.scalar(select(Event));s=db.scalar(select(SyncState).where(SyncState.slot=='mgsu'))
        assert e.active and json.loads(e.override_json)['cancelled']
        assert s.status=='empty' and s.successful_at==last_success


def test_partial_refresh_does_not_delete_other_weeks():
    main.apply_remote(1,'mgsu',[lesson(),lesson('2026-11-10')],'raspika_web')
    main.apply_remote(1,'mgsu',[lesson()],'raspika_web',coverage=('2026-11-02','2026-11-08'),limited=True)
    with SessionLocal() as db:
        assert db.scalar(select(Event).where(Event.date=='2026-11-10')).active


def test_provider_switch_is_scoped_and_preserves_manual_edits(monkeypatch):
    main.apply_remote(1,'mgsu',[lesson(),lesson('2026-11-10')],'raspika')
    with SessionLocal() as db:
        old=db.scalar(select(Event).where(Event.date=='2026-11-03'))
        old.override_json='{"cancelled":true}';db.commit()
    monkeypatch.setattr(main,'get_mai',Mock(side_effect=sources.SourceError('нет данных')))
    monkeypatch.setattr(main,'get_mgsu_public',public_batch)
    main._sync_couple(1)
    with SessionLocal() as db:
        old=db.scalar(select(Event).where(Event.source=='raspika',Event.date=='2026-11-03'))
        assert not old.active and main.serialize_event(old)['cancelled']
        assert db.scalar(select(Event).where(Event.source=='raspika',Event.date=='2026-11-10')).active


def mock_api(monkeypatch,payload,groups=None):
    monkeypatch.setenv('RASPIKA_API_KEY','test-key-do-not-log')
    groups=groups or {'data':[{'name':'ИПГС 2-64 · ВЫСОТ','id':'from-directory'}]}
    get=Mock(side_effect=[Mock(json=lambda:groups),Mock(json=lambda:payload)])
    monkeypatch.setattr(sources,'client_get',get)
    return get


def api_row(**updates):
    row=dict(date='2026-11-10',start='10:00',end='11:20',subject='Строительные материалы',subgroup=1)
    row.update(updates);return row


def test_api_keeps_common_and_first_subgroup_and_uses_directory_id(monkeypatch):
    get=mock_api(monkeypatch,dict(data=[api_row(subgroup=0,subject='Общая лекция'),api_row(),api_row(subgroup=2)]))
    batch=sources.get_mgsu('2026-11-09','2026-11-15')
    assert len(batch.events)==2 and not batch.limited
    assert batch.coverage_to=='2026-11-10'
    assert get.call_args.kwargs['params']['group']=='from-directory'
    assert get.call_args.kwargs['headers']=={'X-Api-Key':'test-key-do-not-log'}


@pytest.mark.parametrize('payload',[
    {'data':[]}, {'data':[api_row()], 'stale':True}, {'data':'bad'},
    {'data':[api_row(start='',end='')]}, {'data':[api_row(date='2026-02-30')]},
    {'data':[api_row(start='25:00')]}, {'data':[api_row(start='12:00',end='11:00')]},
    {'data':[api_row(date='2026-11-16')]}, {'data':[api_row(subgroup=3)]},
    {'data':[api_row()], 'group':'another-group'},
])
def test_api_empty_stale_or_invalid_is_not_success(monkeypatch,payload):
    mock_api(monkeypatch,payload)
    with pytest.raises(sources.SourceError):sources.get_mgsu('2026-11-09','2026-11-15')


def test_ambiguous_group_is_not_guessed(monkeypatch):
    mock_api(monkeypatch,{},dict(data=[{'name':'ИПГС 2-64 · A','id':'one'},{'name':'ИПГС 2-64 · B','id':'two'}]))
    with pytest.raises(sources.SourceError):sources.get_mgsu('2026-11-09','2026-11-15')

def test_unauthorized_api_key_has_safe_explanation(monkeypatch):
    import requests
    monkeypatch.setenv('RASPIKA_API_KEY','never-expose-this-key')
    response=requests.Response();response.status_code=401
    monkeypatch.setattr(sources,'client_get',Mock(side_effect=requests.HTTPError('private-url',response=response)))
    with pytest.raises(sources.SourceError,match='не приняла API-ключ') as error:
        sources.get_mgsu('2026-11-09','2026-11-15')
    assert 'never-expose' not in str(error.value)


def test_changed_public_markup_is_not_partial_success():
    html='<h1>Расписание ИПГС 2-64</h1><h3>Понедельник, 2 ноября</h3><ul><li>Нет понятного времени</li></ul>'
    with pytest.raises(sources.SourceError):sources.parse_raspika_batch(html,today=date(2026,11,7))


def test_legacy_status_is_invalidated_once():
    with SessionLocal() as db:
        db.add(SyncState(couple_id=1,slot='mgsu',status='ok',message='raspika_web',count=14))
        db.commit()
    async def start():
        async with main.lifespan(main.app):pass
    asyncio.run(start())
    with SessionLocal() as db:
        assert db.scalar(select(SyncState)).status=='unverified'


def test_network_error_does_not_expose_credentials(monkeypatch):
    monkeypatch.setattr(main,'get_mai',Mock(side_effect=RuntimeError('https://example/?key=secret')))
    monkeypatch.setattr(main,'get_mgsu_public',Mock(side_effect=RuntimeError('header test-key-do-not-log')))
    result=main._sync_couple(1)
    assert 'secret' not in json.dumps(result) and 'test-key-do-not-log' not in json.dumps(result)

def test_schedule_endpoint_exposes_period_and_failed_recheck(monkeypatch):
    from fastapi.testclient import TestClient
    from app.database import User
    async def no_sync(*args,**kwargs):return {}
    monkeypatch.setattr(main,'sync_couple',no_sync)
    with SessionLocal() as db:
        db.add(User(couple_id=1,username='test-user',display_name='Тест',slot='mai',password_hash=main.pw_hash('test-password')))
        db.commit()
    with TestClient(main.app) as client:
        headers={'Origin':'http://localhost:8000','X-Vmeste-Client':'pwa'}
        assert client.post('/api/login',json={'username':'test-user','password':'test-password'},headers=headers).status_code==200
        batch=public_batch()
        main.apply_remote(1,'mgsu',batch.events,'raspika_web',coverage=(batch.coverage_from,batch.coverage_to),limited=True)
        data=client.get('/api/schedule?date_from=2026-11-09&date_to=2026-11-15').json()
        assert data['events']==[]
        assert data['sources']['mgsu']['status']=='partial'
        assert data['sources']['mgsu']['coverage_to']=='2026-11-08'
        main.set_sync_error(1,'mgsu','Источник временно недоступен')
        data=client.get('/api/schedule?date_from=2026-11-02&date_to=2026-11-08').json()
        assert len(data['events'])==14
        assert data['sources']['mgsu']['reliable'] is False
        assert data['sources']['mgsu']['loaded'] is True


def test_saved_pdf_covers_empty_weekend_without_claiming_fresh_download(monkeypatch):
    from fastapi.testclient import TestClient
    from app.database import User
    async def no_sync(*a,**k):return {}
    monkeypatch.setattr(main,'sync_couple',no_sync)
    with SessionLocal() as db:
        db.add(User(couple_id=1,username='pdf-coverage',display_name='Тест',slot='mgsu',password_hash=main.pw_hash('test-password')))
        db.commit()
    main.apply_remote(1,'mgsu',[lesson('2026-11-03')],'mgsu_pdf',coverage=('2026-11-02','2026-12-20'),sync_status='baseline',source_note='Сохранённый официальный PDF')
    with TestClient(main.app) as client:
        headers={'Origin':'http://localhost:8000','X-Vmeste-Client':'pwa'}
        assert client.post('/api/login',json={'username':'pdf-coverage','password':'test-password'},headers=headers).status_code==200
        for day in ['2026-11-07','2026-11-08','2026-11-14','2026-11-15']:
            data=client.get(f'/api/schedule?date_from={day}&date_to={day}').json()
            assert data['events']==[]
            source=data['sources']['mgsu']
            assert source['loaded'] is True and source['reliable'] is False
            assert source['status']=='baseline' and source['coverage_from']<=day<=source['coverage_to']
        main.set_sync_error(1,'mgsu','Проверка не удалась')
        source=client.get('/api/schedule').json()['sources']['mgsu']
        assert source['loaded'] is True and source['reliable'] is False and source['status']=='error'


def test_first_failed_import_is_not_loaded(monkeypatch):
    from fastapi.testclient import TestClient
    from app.database import User
    async def no_sync(*a,**k):return {}
    monkeypatch.setattr(main,'sync_couple',no_sync)
    with SessionLocal() as db:
        db.add(User(couple_id=1,username='no-coverage',display_name='Тест',slot='mgsu',password_hash=main.pw_hash('test-password')));db.commit()
    main.set_sync_error(1,'mgsu','Не удалось загрузить')
    with TestClient(main.app) as client:
        client.post('/api/login',json={'username':'no-coverage','password':'test-password'},headers={'Origin':'http://localhost:8000','X-Vmeste-Client':'pwa'})
        source=client.get('/api/schedule').json()['sources']['mgsu']
        assert source['loaded'] is False and source['successful_at'] is None

def test_provider_change_transfers_unambiguous_manual_override(monkeypatch):
    old=lesson('2026-11-09',title='КОП Информатика')
    main.apply_remote(1,'mgsu',[old],'raspika_web')
    with SessionLocal() as db:
        ev=db.scalar(select(Event));ev.override_json='{"cancelled":true,"room":"Моя аудитория"}';db.commit()
    new=lesson('2026-11-09',title='Информатика')
    batch=sources.ScheduleBatch([new],'2026-11-09','2026-11-15',provider='mgsu_pdf')
    monkeypatch.setenv('MGSU_SOURCE','pdf')
    monkeypatch.setattr(main,'get_pdf_schedule',lambda *a:batch)
    monkeypatch.setattr(main,'get_mai',Mock(side_effect=sources.SourceError('нет данных')))
    assert main._sync_couple(1)['mgsu']['ok']
    with SessionLocal() as db:
        current=db.scalar(select(Event).where(Event.source=='mgsu_pdf'))
        assert main.serialize_event(current)['cancelled']
        assert main.serialize_event(current)['room']=='Моя аудитория'
        old=db.scalar(select(Event).where(Event.source=='raspika_web'))
        assert not old.active and old.override_json=='{}'

def test_real_pdf_replaces_old_week_and_persists_future_lessons(monkeypatch):
    from app import mgsu_pdf
    pdf=(Path(__file__).parent.parent/'data/mgsu_autumn_2026.pdf').read_bytes()
    main.apply_remote(1,'mgsu',[lesson()],'raspika_web',coverage=('2026-11-02','2026-11-08'),limited=True)
    with SessionLocal() as db:
        db.add(Event(couple_id=1,slot='mgsu',source='manual',date='2026-11-10',start='18:00',end='19:00',title='Моя запись'))
        db.commit()
    monkeypatch.setenv('MGSU_SOURCE','pdf')
    monkeypatch.setattr(main,'get_pdf_schedule',lambda a,b:mgsu_pdf.parse_pdf_schedule(pdf,a,b))
    monkeypatch.setattr(main,'get_mai',Mock(side_effect=sources.SourceError('нет данных')))
    assert main._sync_couple(1)['mgsu']['ok']
    with SessionLocal() as db:
        imported=db.scalars(select(Event).where(Event.source=='mgsu_pdf',Event.date>='2026-11-09',Event.date<='2026-11-15',Event.active.is_(True))).all()
        assert len(imported)==16
        assert db.scalar(select(Event).where(Event.source=='manual')).active
        assert not db.scalar(select(Event).where(Event.source=='raspika_web')).active
        state=db.scalar(select(SyncState).where(SyncState.slot=='mgsu'))
        assert state.status=='ok' and state.coverage_to=='2026-12-20'

def test_upgrade_from_partial_week_requests_semester_recheck(monkeypatch):
    monkeypatch.setenv('MGSU_SOURCE','auto')
    with SessionLocal() as db:
        db.add(SyncState(couple_id=1,slot='mgsu',status='partial',message='Доступна только публичная неделя; другие даты не подтверждены.'))
        db.commit()
    async def start():
        async with main.lifespan(main.app):pass
    asyncio.run(start())
    with SessionLocal() as db:
        assert db.scalar(select(SyncState)).status=='unverified'

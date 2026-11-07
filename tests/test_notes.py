import json
from datetime import datetime,timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app import main,sources
from app.database import Base,engine,SessionLocal,User,Event

H={'Origin':'http://localhost:8000','X-Vmeste-Client':'pwa'}

@pytest.fixture
def pair(monkeypatch):
    Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
    async def no_sync(*a,**k):return {}
    monkeypatch.setattr(main,'sync_couple',no_sync)
    class FixedDate(datetime):
        @classmethod
        def now(cls,tz=None):return datetime(2026,11,7,12,tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(main,'datetime',FixedDate)
    with TestClient(main.app) as one,TestClient(main.app) as two:
        data={'username':'one','display_name':'Первый','password':'very-secure-pass','mode':'create'}
        assert one.post('/api/register',json=data,headers=H).status_code==200
        profile=one.get('/api/me').json()
        data.update(username='two',display_name='Второй',mode='join',invite_code=profile['invite_code'])
        assert two.post('/api/register',json=data,headers=H).status_code==200
        with SessionLocal() as db:cid=db.get(User,profile['user']['id']).couple_id
        yield one,two,cid

def payload(note='контрольная'):
    return dict(slot='mai',date='2026-11-10',start='10:00',end='11:20',title='Математика',room='123',lesson_type='ПЗ',teacher='',note=note)

def imported(cid):
    row={k:v for k,v in payload().items() if k not in ('slot','note')}
    row['subgroup']=0;row['external_id']=sources.stable_id('mai',row)
    main.apply_remote(cid,'mai',[row],'mai_site')
    return row

def find(client):
    return client.get('/api/schedule?date_from=2026-11-10&date_to=2026-11-10').json()['events']

def edit_body(event,**changes):
    body={k:event[k] for k in ('date','start','end','title','room','lesson_type','teacher','revision','cancelled')}
    body.update(changes);return body

def test_manual_note_is_shared_and_can_include_newlines(pair):
    one,two,cid=pair
    text='Контрольная\nПринести чертёж'
    created=one.post('/api/events',json=payload(text),headers=H)
    assert created.status_code==200 and created.json()['note']==text
    assert find(two)[0]['note']==text

def test_imported_note_survives_source_refresh(pair):
    one,two,cid=pair;row=imported(cid)
    event=find(one)[0]
    response=one.put(f'/api/events/{event["id"]}',json=edit_body(event,note='Контрольная'),headers=H)
    assert response.status_code==200
    row['room']='456';main.apply_remote(cid,'mai',[row],'mai_site')
    again=find(two)[0]
    assert again['note']=='Контрольная' and again['room']=='456'

@pytest.mark.parametrize('provider',['manual','imported'])
def test_older_client_preserves_note_and_explicit_empty_clears_it(pair,provider):
    one,two,cid=pair
    if provider=='manual':one.post('/api/events',json=payload(),headers=H)
    else:
        imported(cid);event=find(one)[0]
        one.put(f'/api/events/{event["id"]}',json=edit_body(event,note='контрольная'),headers=H)
    event=find(one)[0]
    response=two.put(f'/api/events/{event["id"]}',json=edit_body(event,room='789'),headers=H)
    assert response.status_code==200 and response.json()['note']=='контрольная'
    response=one.put(f'/api/events/{event["id"]}',json=edit_body(response.json(),note=''),headers=H)
    assert response.status_code==200 and response.json()['note']==''

def test_note_length_and_concurrent_edit_are_checked(pair):
    one,two,cid=pair
    assert one.post('/api/events',json=payload('x'*1001),headers=H).status_code==422
    created=one.post('/api/events',json=payload(),headers=H).json()
    assert two.put(f'/api/events/{created["id"]}',json=edit_body(created,note='Другая заметка'),headers=H).status_code==200
    assert one.put(f'/api/events/{created["id"]}',json=edit_body(created,note='Старое изменение'),headers=H).status_code==409
    assert find(one)[0]['note']=='Другая заметка'

def test_note_keeps_disappeared_imported_lesson_visible(pair):
    one,two,cid=pair;row=imported(cid);event=find(one)[0]
    one.put(f'/api/events/{event["id"]}',json=edit_body(event,note='Уточнить у преподавателя'),headers=H)
    row={**row,'title':'Другой предмет'};row['external_id']=sources.stable_id('mai',row)
    main.apply_remote(cid,'mai',[row],'mai_site')
    old=next(e for e in find(two) if e['id']==event['id'])
    assert old['conflict'] and old['note']=='Уточнить у преподавателя'

def test_schedule_exposes_verified_calendar_and_legacy_event_empty_note(pair):
    one,two,cid=pair
    body=payload();body.pop('note')
    assert one.post('/api/events',json=body,headers=H).json()['note']==''
    response=one.get('/api/schedule').json()
    c=response['academic_calendars']['mgsu']
    assert c['week_one_monday']=='2026-08-31' and c['weeks']==16
    assert c['term_end']=='2026-12-20'
    assert 'mai' not in response['academic_calendars']

import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app import main
from app.database import Base,engine,SessionLocal,Couple,User,Event,DayAttendance,LessonAttendance

H={'Origin':'http://localhost:8000','X-Vmeste-Client':'pwa'}
RANGE='/api/schedule?date_from=2026-11-01&date_to=2026-11-30'

@pytest.fixture
def pair(monkeypatch):
    Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
    async def no_sync(*a,**k):return {}
    monkeypatch.setattr(main,'sync_couple',no_sync)
    with TestClient(main.app) as one,TestClient(main.app) as two:
        assert one.post('/api/register',headers=H,json={'username':'attendance_one','display_name':'Я','password':'very-secure-pass','mode':'create'}).status_code==200
        profile=one.get('/api/me').json()
        assert two.post('/api/register',headers=H,json={'username':'attendance_two','display_name':'Она','password':'very-secure-pass','mode':'join','invite_code':profile['invite_code']}).status_code==200
        with SessionLocal() as db:
            cid=db.get(User,profile['user']['id']).couple_id
            for eid,slot,day in [(1,'mai','2026-11-09'),(2,'mai','2026-11-09'),(3,'mgsu','2026-11-09'),(4,'mai','2026-11-16')]:
                db.add(Event(id=eid,couple_id=cid,slot=slot,date=day,start='10:00' if eid!=2 else '12:00',end='11:20' if eid!=2 else '13:20',title='Физика',source='mai_site' if slot=='mai' else 'mgsu_pdf',external_id=str(eid),override_json='{"note":"контрольная"}'))
            db.commit()
        yield one,two,cid

def rows(client):return client.get(RANGE).json()['events']
def event(client,eid):return next(e for e in rows(client) if e['id']==eid)
def mark(client,eid,not_going=True,**extra):
    e=event(client,eid)
    return client.put(f'/api/events/{eid}/attendance',headers=H,json={'not_going':not_going,'revision':e['revision'],'scope_token':e['attendance_scope'],**extra})
def day(client,date='2026-11-09',not_going=True,**extra):
    return client.put('/api/attendance/days/'+date,headers=H,json={'not_going':not_going,**extra})

def test_single_occurrence_shared_visibility_owner_only_and_undo(pair):
    one,two,_=pair
    assert mark(one,1).status_code==200
    assert mark(one,1).status_code==200
    for client in [one,two]:
        displayed=rows(client);assert len(displayed)==4
        assert {e['id'] for e in displayed if e['not_going']}=={1}
        assert not event(client,1)['cancelled'] and event(client,1)['note']=='контрольная'
    assert event(one,1)['can_mark_attendance'] is True
    assert event(two,1)['can_mark_attendance'] is False
    assert mark(two,1,False).status_code==403
    assert mark(one,3).status_code==403
    assert mark(two,3).status_code==200
    assert mark(one,1,False).status_code==200
    assert {e['id'] for e in rows(one) if e['not_going']}=={3}
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(LessonAttendance))==2
        assert db.scalar(select(func.count()).select_from(Event))==4

def test_whole_day_own_slot_future_import_and_single_mark_survives_day_undo(pair):
    one,two,cid=pair
    assert mark(one,1).status_code==200
    assert day(one).status_code==200
    assert day(one).status_code==200
    assert {e['id'] for e in rows(two) if e['not_going']}=={1,2}
    assert event(two,1)['day_not_going'] and event(two,1)['lesson_not_going']
    assert event(two,2)['day_not_going'] and not event(two,2)['lesson_not_going']
    # Same date, new source event imported later also inherits the whole-day mark.
    with SessionLocal() as db:
        db.add(Event(id=5,couple_id=cid,slot='mai',date='2026-11-09',start='15:00',end='16:00',title='Новая пара',source='mai_site',external_id='5'));db.commit()
    assert event(two,5)['not_going']
    assert day(two).json()['slot']=='mgsu'
    assert day(one,not_going=False).status_code==200
    assert {e['id'] for e in rows(one) if e['not_going']}=={1,3}
    with SessionLocal() as db:assert db.scalar(select(func.count()).select_from(DayAttendance))==2

@pytest.mark.parametrize('extra',[{'owner_id':2},{'slot':'mgsu'},{'couple_id':999}])
def test_cannot_forge_day_or_lesson_owner(pair,extra):
    one,_,_=pair
    assert day(one,**extra).status_code==422
    assert mark(one,1,**extra).status_code==422
    assert one.get(RANGE).json()['attendance_days']==[]

@pytest.mark.parametrize('date',['2026-02-30','20261109','1999-12-31'])
def test_invalid_day_rejected(pair,date):
    assert day(pair[0],date).status_code==422

def test_revision_and_source_scope_guard(pair):
    one,_,_=pair
    e=event(one,1);p={'not_going':True,'revision':e['revision'],'scope_token':e['attendance_scope']}
    with SessionLocal() as db:db.get(Event,1).start='10:15';db.commit()
    assert one.put('/api/events/1/attendance',headers=H,json=p).status_code==409
    assert mark(one,1,revision=999).status_code==409
    assert mark(one,1,scope_token='0'*64).status_code==409

def test_move_override_matches_effective_day_and_reimport_preserves_mark(pair):
    one,_,cid=pair
    assert mark(one,1).status_code==200
    with SessionLocal() as db:
        original=db.get(Event,1)
        db.add(Event(id=5,couple_id=cid,slot='mai',date=original.date,start=original.start,end=original.end,title=original.title,source='new_provider',external_id='new'))
        original.override_json=json.dumps({'date':'2026-11-10','title':'Моя правка','note':'контрольная'})
        db.commit()
    assert event(one,1)['not_going'] and event(one,5)['not_going']
    assert day(one,'2026-11-09').status_code==200
    assert event(one,1)['day_not_going'] is False
    assert day(one,'2026-11-10').status_code==200
    assert event(one,1)['day_not_going'] is True
    assert event(one,1)['title']=='Моя правка'

def test_cancel_and_ordinary_edit_do_not_clear_personal_attendance(pair):
    one,_,cid=pair
    assert mark(one,1).status_code==200
    e=event(one,1)
    payload={k:e[k] for k in ('revision','date','start','end','title','lesson_type','room','teacher','note')}
    payload['cancelled']=True
    assert one.put('/api/events/1',headers=H,json=payload).status_code==200
    e=event(one,1);assert e['cancelled'] and e['not_going'] and e['note']=='контрольная'
    assert mark(one,1,False).status_code==200
    assert event(one,1)['cancelled'] and not event(one,1)['not_going']
    # Editing a manually added class retains its personal attendance identity.
    manual=one.post('/api/events',headers=H,json={'slot':'mai','date':'2026-11-10','start':'15:00','end':'16:00','title':'Ручная'}).json()
    assert mark(one,manual['id']).status_code==200
    e=event(one,manual['id']);p={k:e[k] for k in ('revision','date','start','end','title','lesson_type','room','teacher')};p['title']='Новое название'
    assert one.put('/api/events/'+str(e['id']),headers=H,json=p).status_code==200
    assert event(one,e['id'])['not_going']

def test_other_couple_auth_origin_notify_and_coverage(pair):
    one,_,cid=pair
    with SessionLocal() as db:
        c=Couple();db.add(c);db.flush();u=User(couple_id=c.id,username='other_attendance',display_name='Other',password_hash='unused',slot='mai');db.add(u);db.flush()
        db.add(Event(id=10,couple_id=c.id,slot='mai',date='2026-11-09',start='10:00',end='11:00',title='Private'))
        db.add(DayAttendance(couple_id=c.id,owner_id=u.id,date='2026-11-09',not_going=True));db.commit()
    assert one.put('/api/events/10/attendance',headers=H,json={'not_going':True,'revision':1,'scope_token':'0'*64}).status_code==404
    with TestClient(main.app) as anonymous:
        assert anonymous.put('/api/attendance/days/2026-11-09',headers=H,json={'not_going':True}).status_code==401
        assert anonymous.put('/api/events/1/attendance',headers=H,json={'not_going':True,'revision':1,'scope_token':'0'*64}).status_code==401
    assert one.put('/api/attendance/days/2026-11-09',headers={**H,'Origin':'https://evil.test'},json={'not_going':True}).status_code==403
    assert one.put('/api/attendance/days/2026-11-09',headers=H,json={'not_going':'true'}).status_code==422
    messages=[]
    class Socket:
        async def send_json(self,data):messages.append(data)
    main._connections[cid]={Socket()}
    try:
        assert mark(one,1).status_code==200 and day(one).status_code==200
        assert messages==[{'type':'attendance_changed'}]*2
    finally:main._connections.pop(cid,None)
    result=one.get(RANGE).json()
    assert result['attendance_complete'] is True
    assert result['attendance_coverage']=={'from':'2026-11-01','to':'2026-11-30'}
    assert len(result['attendance_days'])==1
    assert one.get('/api/schedule?date_from=2026-11-10&date_to=2026-11-10').json()['attendance_days']==[]

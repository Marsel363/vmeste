import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app import main
from app.database import Base,engine,SessionLocal,Event,User,Couple,LessonExclusion,PersonalTask
from app.exclusions import subject_key

H={'Origin':'http://localhost:8000','X-Vmeste-Client':'pwa'}
RANGE='/api/schedule?date_from=2026-10-26&date_to=2026-12-01'

@pytest.fixture
def calendar(monkeypatch):
    Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
    async def no_sync(*a,**kw):return {}
    monkeypatch.setattr(main,'sync_couple',no_sync)
    with TestClient(main.app) as one,TestClient(main.app) as two:
        def signup(c,name,**rest):
            return c.post('/api/register',headers=H,json={'username':name,'display_name':name,'password':'very-secure-pass','mode':'create',**rest})
        assert signup(one,'exclude_owner').status_code==200
        profile=one.get('/api/me').json()
        assert signup(two,'exclude_partner',mode='join',invite_code=profile['invite_code']).status_code==200
        with SessionLocal() as db:
            owner=db.get(User,profile['user']['id']);cid=owner.couple_id
            items=[('2026-11-02','09:00','10:30','Теория вероятностей','mai'),('2026-11-09','09:00','10:30','ТЕОРИЯ   ВЕРОЯТНОСТЕЙ','mai'),('2026-11-16','09:00','10:30','Теория вероятностей','mai'),('2026-11-09','10:45','12:15','Теория вероятностей','mai'),('2026-11-10','09:00','10:30','Теория вероятностей','mai'),('2026-11-16','09:00','10:30','Физика','mai'),('2026-11-09','09:00','10:30','Теория вероятностей','mgsu')]
            for index,(day,start,end,title,slot) in enumerate(items,1):
                db.add(Event(id=index,couple_id=cid,slot=slot,date=day,start=start,end=end,title=title,source='mai_site',external_id=str(index),lesson_type='ЛК' if index%2 else 'ПЗ',override_json=json.dumps({'note':'Контрольная','room':'Исправленная аудитория'},ensure_ascii=False) if index==1 else '{}'))
            db.add(PersonalTask(couple_id=cid,owner_id=owner.id,date='2026-11-09',title='Теория вероятностей',start='09:00',end='10:30',busy=True))
            db.commit()
        yield one,two,cid

def exclude(client,event_id=2):
    event=next(e for e in client.get(RANGE).json()['events'] if e['id']==event_id)
    return client.post(f'/api/events/{event_id}/exclude-series',headers=H,json={'revision':event['revision'],'scope_token':event['exclusion_scope']['token']})

def test_excludes_all_dates_only_matching_subject_weekday_time_and_slot(calendar):
    one,two,cid=calendar
    before=one.get(RANGE).json();assert len(before['events'])==7
    response=exclude(one);assert response.status_code==200,response.text
    rule=response.json();assert rule['weekday']==0 and rule['start']=='09:00' and rule['end']=='10:30'
    for client in [one,two]:
        after=client.get(RANGE).json()
        assert {e['id'] for e in after['events']}=={4,5,6,7}
        assert after['exclusions_complete'] is True and len(after['exclusions'])==1
        assert len(after['tasks'])==1
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Event))==7
        assert json.loads(db.get(Event,1).override_json)['note']=='Контрольная'
    assert two.request('DELETE',f'/api/lesson-exclusions/{rule["id"]}',json={},headers=H).status_code==200
    restored=one.get(RANGE).json()
    assert len(restored['events'])==7 and restored['exclusions']==[]
    assert next(e for e in restored['events'] if e['id']==1)['room']=='Исправленная аудитория'

def test_future_import_new_provider_id_and_user_overrides_do_not_revive_exclusion(calendar):
    one,_,cid=calendar
    rule=exclude(one).json()
    # Source identities remain stable even when a one-off override changes title/date/time.
    with SessionLocal() as db:
        event=db.get(Event,3)
        event.override_json=json.dumps({'date':'2026-11-17','start':'14:00','end':'15:00','title':'Личная пометка'})
        db.commit()
    assert 3 not in {e['id'] for e in one.get(RANGE).json()['events']}
    incoming={'external_id':'new-provider-id','date':'2026-11-23','start':'09:00','end':'10:30','title':'Теория вероятностей','room':'Новая','teacher':'','lesson_type':'ЛК','subgroup':0}
    main.apply_remote(cid,'mai',[incoming],'another_source',coverage=('2026-11-23','2026-11-29'))
    assert all(e['date']!='2026-11-23' for e in one.get(RANGE).json()['events'])
    assert one.request('DELETE',f'/api/lesson-exclusions/{rule["id"]}',json={},headers=H).status_code==200
    after=one.get(RANGE).json()['events']
    assert any(e['date']=='2026-11-23' and e['room']=='Новая' for e in after)
    assert next(e for e in after if e['id']==3)['title']=='Личная пометка'

def test_token_guards_source_changes_without_revision_and_creation_is_idempotent(calendar):
    one,two,_=calendar
    event=next(e for e in one.get(RANGE).json()['events'] if e['id']==2)
    payload={'revision':event['revision'],'scope_token':event['exclusion_scope']['token']}
    with SessionLocal() as db:
        db.get(Event,2).start='09:15';db.commit()
    assert one.post('/api/events/2/exclude-series',headers=H,json=payload).status_code==409
    event=next(e for e in one.get(RANGE).json()['events'] if e['id']==2)
    payload['scope_token']=event['exclusion_scope']['token']
    first=one.post('/api/events/2/exclude-series',headers=H,json=payload)
    second=two.post('/api/events/2/exclude-series',headers=H,json=payload)
    assert first.status_code==200 and first.json()==second.json()
    assert len(one.get(RANGE).json()['exclusions'])==1

def test_other_calendar_cannot_create_see_or_restore_rules(calendar):
    one,_,_=calendar
    with SessionLocal() as db:
        c=Couple();db.add(c);db.flush()
        u=User(couple_id=c.id,username='another_calendar',display_name='Other',password_hash='unused',slot='mai');db.add(u);db.flush()
        e=Event(couple_id=c.id,slot='mai',date='2026-11-09',start='09:00',end='10:30',title='Private');db.add(e);db.flush()
        rule=LessonExclusion(couple_id=c.id,created_by=u.id,slot='mai',weekday=0,start='09:00',end='10:30',title='Private',title_key='private');db.add(rule);db.commit();eid=e.id;rid=rule.id
    assert one.get(RANGE).json()['exclusions']==[]
    assert one.post(f'/api/events/{eid}/exclude-series',headers=H,json={'revision':1,'scope_token':'0'*64}).status_code==404
    assert one.request('DELETE',f'/api/lesson-exclusions/{rid}',headers=H,json={}).status_code==404

def test_unauthorized_forged_scope_origin_and_notifications(calendar):
    one,_,cid=calendar
    event=one.get(RANGE).json()['events'][0]
    payload={'revision':event['revision'],'scope_token':event['exclusion_scope']['token']}
    with TestClient(main.app) as anonymous:
        assert anonymous.post('/api/events/1/exclude-series',headers=H,json=payload).status_code==401
        assert anonymous.request('DELETE','/api/lesson-exclusions/1',headers=H,json={}).status_code==401
    assert one.post('/api/events/1/exclude-series',headers=H,json={**payload,'slot':'mgsu'}).status_code==422
    assert one.post('/api/events/1/exclude-series',headers=H,json={**payload,'scope_token':'0'*64}).status_code==409
    assert one.post('/api/events/1/exclude-series',headers={**H,'Origin':'https://other.example'},json=payload).status_code==403
    messages=[]
    class Socket:
        async def send_json(self,data):messages.append(data)
    main._connections[cid]={Socket()}
    try:
        rule=one.post('/api/events/1/exclude-series',headers=H,json=payload).json()
        assert one.request('DELETE',f'/api/lesson-exclusions/{rule["id"]}',headers=H,json={}).status_code==200
        assert messages==[{'type':'exclusions_changed'}]*2
    finally:main._connections.pop(cid,None)

def test_subject_normalization_is_conservative():
    assert subject_key(' ЛК Теория вероятностей ')!='теория вероятностей' # only documented source prefixes are ignored
    assert subject_key('КОП  Теория\nвероятностей')==subject_key('теория вероятностей')
    assert subject_key('C++')!=subject_key('C#')


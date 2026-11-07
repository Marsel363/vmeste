import os
import tempfile
from pathlib import Path
os.environ['SECRET_KEY']='test-secret'
os.environ['ENABLE_SCHEDULER']='0'
from fastapi.testclient import TestClient
from sqlalchemy import delete
from app.main import app, apply_remote
from app.database import Base, engine, Event, SessionLocal
from app.sources import parse_mai, parse_raspika_public

H={'Origin':'http://localhost:8000','X-Vmeste-Client':'pwa'}

MAI_HTML='''<html><h3>понедельник, 9 ноября 2026 42%</h3><h6>ЛК 9:00 → 10:30 ГУК В-221</h6><h6>Социология</h6><a>Перкова</a><h6>ЛК 10:45 → 12:15 ГУК Б-649</h6><h6>Теория вероятностей и математическая статистика</h6><h3>вторник, 10 ноября 2026</h3><h6>ПЗ 13:00 → 14:30 Орш. В-103</h6><h6>Иностранный язык</h6></html>'''
RASP_HTML='''<h1>Расписание ИПГС 2-64</h1><p>Неделя с 2 ноября по 8 ноября</p><h3>Понедельник, 2 ноября</h3><ul><li>11:30<br>12:50<br>Строительные материалы<br>Практика · Епихин С.Д. · 124/КМК</li><li>13:00<br>14:20<br>Строительные материалы подгр. 2<br>Лаборатор. · Каддо М.Б. · 124/КМК</li><li>14:30<br>15:50<br>Строительные материалы подгр. 1<br>Лаборатор. · Каддо М.Б. · 124/КМК</li></ul><h2>Другие группы</h2>'''

def setup_function():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

def signup(client,username,mode='create',code=None):
    return client.post('/api/register',json={'username':username,'display_name':username,'password':'very-secure-pass','mode':mode,'invite_code':code},headers=H)

def test_parsers():
    mai=parse_mai(MAI_HTML)
    assert len(mai)==3
    assert mai[0]['title']=='Социология'
    assert mai[0]['date']=='2026-11-09'
    assert mai[0]['room']=='ГУК В-221'
    rsp=parse_raspika_public(RASP_HTML)
    assert len(rsp)==2
    assert {x['subgroup'] for x in rsp}=={0,1}
    assert all('подгр. 2' not in x['title'] for x in rsp)

def test_couple_and_manual_events(monkeypatch):
    import app.main as main
    async def no_sync(*a,**k):return {'skipped':True}
    monkeypatch.setattr(main,'sync_couple',no_sync)
    with TestClient(app) as one, TestClient(app) as two:
        assert signup(one,'first').status_code==200
        profile=one.get('/api/me').json()
        assert profile['user']['slot']=='mai'
        code=profile['invite_code']
        assert code
        assert signup(two,'second','join',code).status_code==200
        assert two.get('/api/me').json()['user']['slot']=='mgsu'
        event={'slot':'mgsu','date':'2026-11-10','start':'14:30','end':'15:50','title':'Лабораторная','room':'121'}
        created=two.post('/api/events',json=event,headers=H)
        assert created.status_code==200,created.text
        eid=created.json()['id']
        assert eid in [x['id'] for x in one.get('/api/schedule?date_from=2026-11-01&date_to=2026-11-30').json()['events']]
        full=created.json()
        edited={k:full.get(k,'') for k in ['date','start','end','title','lesson_type','room','teacher']}
        edited.update({'revision':full['revision'],'cancelled':True})
        res=one.put(f'/api/events/{eid}',json=edited,headers=H)
        assert res.status_code==200,res.text
        assert res.json()['cancelled'] is True
        assert two.get('/api/schedule?date_from=2026-11-01&date_to=2026-11-30').json()['events'][0]['cancelled']
        assert one.put(f'/api/events/{eid}',json=edited,headers=H).status_code==409
        assert two.request('DELETE',f'/api/events/{eid}',headers=H,json={}).status_code==200
        assert not one.get('/api/schedule?date_from=2026-11-01&date_to=2026-11-30').json()['events']

def test_sync_preserves_manual_edits(monkeypatch):
    import app.main as main
    async def no_sync(*a,**k):return {}
    monkeypatch.setattr(main,'sync_couple',no_sync)
    with TestClient(app) as one:
        assert signup(one,'third').status_code==200
        cid=one.get('/api/me').json()['user']['id']
        with SessionLocal() as db:
            from app.database import User
            couple_id=db.get(User,cid).couple_id
        from app.sources import stable_id
        lesson={'date':'2026-11-10','start':'13:00','end':'14:30','title':'Экология','lesson_type':'ПЗ','teacher':'','room':'А-321','subgroup':0}
        lesson['external_id']=stable_id('mai',lesson)
        apply_remote(couple_id,'mai',[lesson],'mai_site')
        e=one.get('/api/schedule?date_from=2026-11-09&date_to=2026-11-12').json()['events'][0]
        update={k:e.get(k,'') for k in ['date','start','end','title','lesson_type','room','teacher']}
        update['revision']=e['revision'];update['cancelled']=True
        assert one.put(f'/api/events/{e["id"]}',json=update,headers=H).status_code==200
        lesson['room']='Б-211'
        apply_remote(couple_id,'mai',[lesson],'mai_site')
        again=one.get('/api/schedule?date_from=2026-11-09&date_to=2026-11-12').json()['events'][0]
        assert again['cancelled'] is True
        assert again['room']=='Б-211'
        assert again['source']=='mai_site'

def test_security(monkeypatch):
    import app.main as main
    async def no_sync(*a,**k):return {}
    monkeypatch.setattr(main,'sync_couple',no_sync)
    with TestClient(app) as client:
        assert client.get('/api/schedule').status_code==401
        bad=client.post('/api/register',json={'username':'no','display_name':'x','password':'short','mode':'create'},headers=H)
        assert bad.status_code==422
        forbidden=client.post('/api/register',json={'username':'second','display_name':'x','password':'password12345','mode':'create'},headers={'Origin':'https://evil.example','X-Vmeste-Client':'pwa'})
        assert forbidden.status_code==403
        assert signup(client,'secure').status_code==200
        assert client.post('/api/events',json={'slot':'mai','date':'2026-11-12','start':'10:00','end':'09:00','title':'Неверная'},headers=H).status_code==422

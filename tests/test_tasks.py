import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app import main
from app.database import Base, engine, SessionLocal, Couple, User, PersonalTask

H={'Origin':'http://localhost:8000','X-Vmeste-Client':'pwa'}
RANGE='/api/schedule?date_from=2026-11-01&date_to=2026-11-30'
EXAMPLE={'title':'Тренировка','date':'2026-11-09','start':'17:00','end':'18:30','busy':True,'note':'Взять форму'}

@pytest.fixture
def pair(monkeypatch):
    Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
    async def no_sync(*a,**kw):return {}
    monkeypatch.setattr(main,'sync_couple',no_sync)
    with TestClient(main.app) as one,TestClient(main.app) as two:
        def signup(c,name,**rest):
            return c.post('/api/register',headers=H,json={'username':name,'display_name':name,'password':'very-secure-pass','mode':'create',**rest})
        assert signup(one,'task_owner').status_code==200
        profile=one.get('/api/me').json()
        assert signup(two,'task_partner',mode='join',invite_code=profile['invite_code']).status_code==200
        yield one,two

def editable(task,**changes):
    return {**{k:task[k] for k in ('title','date','start','end','busy','done','note','revision')},**changes}

def test_owner_only_writes_both_accounts_read_and_persistence(pair):
    one,two=pair
    created=one.post('/api/tasks',json=EXAMPLE,headers=H)
    assert created.status_code==200,created.text
    task=created.json();tid=task['id']
    mine=one.get(RANGE).json();other=two.get(RANGE).json()
    assert mine['tasks_complete'] is True and mine['tasks_coverage']=={'from':'2026-11-01','to':'2026-11-30'}
    assert mine['tasks'][0]['can_edit'] is True
    assert other['tasks'][0]['can_edit'] is False and other['tasks'][0]['slot']=='mai'
    assert task['owner_id']==one.get('/api/me').json()['user']['id']
    assert two.put(f'/api/tasks/{tid}',json=editable(task,title='Чужая правка'),headers=H).status_code==403
    assert two.request('DELETE',f'/api/tasks/{tid}',json={'revision':task['revision']},headers=H).status_code==403
    own=one.put(f'/api/tasks/{tid}',json=editable(task,done=True,note='Готово'),headers=H)
    assert own.status_code==200 and own.json()['revision']==task['revision']+1
    assert two.get(RANGE).json()['tasks'][0]['done'] is True
    assert one.put(f'/api/tasks/{tid}',json=editable(task),headers=H).status_code==409
    assert one.request('DELETE',f'/api/tasks/{tid}',json={'revision':task['revision']},headers=H).status_code==409
    assert one.request('DELETE',f'/api/tasks/{tid}',json={'revision':own.json()['revision']},headers=H).status_code==200
    assert two.get(RANGE).json()['tasks']==[]

@pytest.mark.parametrize('field',['owner_id','couple_id','slot','owner_name','can_edit'])
def test_cannot_choose_or_transfer_owner(pair,field):
    one,two=pair
    assert one.post('/api/tasks',json={**EXAMPLE,field:two.get('/api/me').json()['user']['id']},headers=H).status_code==422
    task=one.post('/api/tasks',json=EXAMPLE,headers=H).json()
    assert one.put(f'/api/tasks/{task["id"]}',json={**editable(task),field:9},headers=H).status_code==422
    assert two.get(RANGE).json()['tasks'][0]['owner_id']==task['owner_id']

@pytest.mark.parametrize('changes',[
    {'title':'  '},{'date':'2026-02-30'},{'date':'20261109'},
    {'start':'17:99'},{'start':'24:00'},{'start':'17:30','end':'17:00'},
    {'start':None},{'start':None,'end':None,'busy':True},{'note':'x'*1001},
])
def test_invalid_tasks_rejected(pair,changes):
    one,_=pair
    assert one.post('/api/tasks',json={**EXAMPLE,**changes},headers=H).status_code==422
    assert one.get(RANGE).json()['tasks']==[]

def test_untimed_reminder_nonblocking_timed_note_and_date_range(pair):
    one,two=pair
    reminder=one.post('/api/tasks',json={'title':'Купить продукты','date':'2026-11-09'},headers=H).json()
    assert reminder['busy'] is False and reminder['start'] is None and reminder['end'] is None
    timed=two.post('/api/tasks',json={**EXAMPLE,'busy':False},headers=H).json()
    assert timed['slot']=='mgsu' and timed['can_edit'] is True and timed['busy'] is False
    assert len(one.get(RANGE).json()['tasks'])==2
    assert one.get('/api/schedule?date_from=2026-11-10&date_to=2026-11-10').json()['tasks']==[]
    assert one.get('/api/schedule?date_from=2026-02-30&date_to=2026-03-01').status_code==422

def test_other_couple_cannot_read_or_mutate(pair):
    one,_=pair
    with SessionLocal() as db:
        couple=Couple();db.add(couple);db.flush()
        user=User(couple_id=couple.id,username='other_calendar',display_name='Другой',slot='mai',password_hash='unused')
        db.add(user);db.flush()
        task=PersonalTask(couple_id=couple.id,owner_id=user.id,date='2026-11-09',title='Секретное дело')
        db.add(task);db.commit();tid=task.id
    assert one.get(RANGE).json()['tasks']==[]
    assert one.put(f'/api/tasks/{tid}',json={**EXAMPLE,'revision':1},headers=H).status_code==404
    assert one.request('DELETE',f'/api/tasks/{tid}',json={'revision':1},headers=H).status_code==404

def test_auth_origin_and_notifications(pair):
    one,_=pair
    with TestClient(main.app) as anonymous:
        assert anonymous.post('/api/tasks',json=EXAMPLE,headers=H).status_code==401
        assert anonymous.put('/api/tasks/1',json={**EXAMPLE,'revision':1},headers=H).status_code==401
        assert anonymous.request('DELETE','/api/tasks/1',json={'revision':1},headers=H).status_code==401
    assert one.post('/api/tasks',json=EXAMPLE,headers={**H,'Origin':'https://other.example'}).status_code==403
    assert one.post('/api/tasks',json=EXAMPLE,headers={'Origin':H['Origin']}).status_code==403
    with SessionLocal() as db:cid=db.get(User,one.get('/api/me').json()['user']['id']).couple_id
    messages=[]
    class Socket:
        async def send_json(self,message):messages.append(message)
    main._connections[cid]={Socket()}
    try:
        task=one.post('/api/tasks',json=EXAMPLE,headers=H).json()
        assert one.put(f'/api/tasks/{task["id"]}',json=editable(task,note='Обновлено'),headers=H).status_code==200
        assert one.request('DELETE',f'/api/tasks/{task["id"]}',json={'revision':2},headers=H).status_code==200
        assert messages==[{'type':'tasks_changed'}]*3
    finally:main._connections.pop(cid,None)

def test_import_does_not_touch_personal_tasks(pair):
    one,_=pair
    task=one.post('/api/tasks',json=EXAMPLE,headers=H).json()
    with SessionLocal() as db:cid=db.get(User,task['owner_id']).couple_id
    day=main.datetime.now(main.MOSCOW).date().isoformat()
    main.apply_remote(cid,'mai',[{'external_id':'task-independence','date':day,'start':'09:00','end':'10:30','title':'Пара','room':'','teacher':'','lesson_type':'ЛК','subgroup':0}],'mai_site')
    assert one.get(RANGE).json()['tasks']==[task]


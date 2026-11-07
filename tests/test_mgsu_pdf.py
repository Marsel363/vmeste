from datetime import date
from pathlib import Path
from unittest.mock import Mock
import pytest
from app import mgsu_pdf, mgsu_web, main, sources
from app.sources import SourceError

PDF=Path(__file__).resolve().parent.parent/'data/mgsu_autumn_2026.pdf'

@pytest.fixture(scope='module')
def parsed():
    return mgsu_pdf.read_pdf(PDF.read_bytes())

def expand(parsed,start,end):
    templates,meta=parsed
    # Reuse parsed geometry so logic tests are fast; the public entry point is
    # also checked separately below on the unmodified PDF bytes.
    from datetime import timedelta
    out=[];day=date.fromisoformat(start)
    while day<=date.fromisoformat(end):
        week=mgsu_pdf.academic_week(day,meta['week_one'])
        for row in templates:
            if row['weekday']==day.weekday() and row['parity'] in (0,1 if week%2 else 2) and week in row['weeks']:
                out.append({'date':day.isoformat(),**row})
        day+=timedelta(days=1)
    return out

def at(rows,day,time):
    return next(x for x in rows if x['date']==day and x['start']==time)

def test_official_academic_week_anchor(parsed):
    _,meta=parsed
    assert meta['week_one']==date(2026,8,31)
    assert meta['weeks']==16 and meta['term_end']==date(2026,12,20)
    assert mgsu_pdf.academic_week(date(2026,11,9),meta['week_one'])==11

def test_real_pdf_odd_week_and_subgroup_one(parsed):
    rows=expand(parsed,'2026-11-09','2026-11-15')
    assert len(rows)==16
    assert at(rows,'2026-11-09','11:30')['title']=='Строительные материалы'
    assert at(rows,'2026-11-09','13:00')['title']=='Информатика'
    assert at(rows,'2026-11-10','10:00')['subgroup']==1
    assert not any(x['subgroup']==2 for x in rows)
    assert not any(x['date']=='2026-11-11' and x['start']=='14:30' for x in rows)
    assert at(rows,'2026-11-13','13:00')['lesson_type']=='Лабораторная'
    assert at(rows,'2026-11-13','13:00')['room']=='301/КМК'
    assert at(rows,'2026-11-10','16:00')['title'].endswith('зданий и сооружений')
    assert at(rows,'2026-11-10','16:00')['room']=='104/КПА'

def test_even_week_uses_lower_cells_and_full_cells_every_week(parsed):
    rows=expand(parsed,'2026-11-16','2026-11-22')
    assert len(rows)==15
    assert at(rows,'2026-11-16','11:30')['title']=='Цифровое моделирование'
    assert at(rows,'2026-11-16','11:30')['lesson_type']=='КОП'
    assert at(rows,'2026-11-16','10:00')['title']=='Высшая математика'
    assert at(rows,'2026-11-17','13:00')['title']=='Сопротивление материалов'
    assert at(rows,'2026-11-20','08:30')['title'].startswith('Физическая культура')
    assert not any(x['date']=='2026-11-20' and x['start']=='11:30' for x in rows)

@pytest.mark.parametrize('start,title',[
    ('2026-09-08','Модуль 2'), # academic week 2
    ('2026-09-22','Модуль 2'), # week 4
    ('2026-10-06','Модуль 1'), # week 6
    ('2026-11-17','Модуль 1'), # week 12
])
def test_explicit_start_and_week_lists(parsed,start,title):
    assert at(expand(parsed,start,start),start,'10:00')['title']==title

def test_practice_and_labs_on_explicit_week_lists(parsed):
    assert at(expand(parsed,'2026-10-30','2026-10-30'),'2026-10-30','13:00')['lesson_type']=='Практика'
    for day in ('2026-11-13','2026-12-11'):
        assert at(expand(parsed,day,day),day,'13:00')['lesson_type']=='Лабораторная'
    lab=at(expand(parsed,'2026-12-08','2026-12-08'),'2026-12-08','14:30')
    assert lab['title']=='Сопротивление материалов' and lab['room']=='103 Г/УЛБ'
    assert lab['lesson_type']=='Лабораторная'

def test_real_public_pdf_entry_point_and_semester_bounds():
    batch=mgsu_pdf.parse_pdf_schedule(PDF.read_bytes(),'2026-11-09','2027-01-10')
    assert batch.provider=='mgsu_pdf' and batch.coverage_to=='2026-12-20'
    assert all(x['date']<='2026-12-20' for x in batch.events)
    assert {x['subgroup'] for x in batch.events}<={0,1}
    assert len([x for x in batch.events if x['date']<='2026-11-15'])==16

def test_unrecognized_pdf_and_conditions_fail_instead_of_guessing():
    with pytest.raises(SourceError):mgsu_pdf.read_pdf(b'<html>not PDF</html>')
    with pytest.raises(SourceError):mgsu_pdf.cell_variants('пр.Предмет через неделю\nСОК')
    with pytest.raises(SourceError):mgsu_pdf.cell_variants('пр.Предмет на 99 нед.\nСОК')
    assert mgsu_pdf.cell_variants('лаб.Строительные материалы- 2 п/г\nдоц.Каддо М.Б. 124 /КМК')==[]

def test_saved_pdf_is_labeled_as_baseline(monkeypatch):
    monkeypatch.setattr(mgsu_pdf,'_official_pdf',Mock(side_effect=RuntimeError('offline')))
    batch=mgsu_pdf.get_pdf_schedule('2026-11-09','2026-11-15')
    assert batch.status=='baseline' and len(batch.events)==16
    assert 'не удалось обновить' in batch.note

def test_auto_prefers_official_pdf_without_api_key(monkeypatch):
    monkeypatch.setenv('MGSU_SOURCE','auto')
    monkeypatch.delenv('RASPIKA_API_KEY',raising=False)
    result=object()
    monkeypatch.setattr(main,'get_pdf_schedule',lambda *a:result)
    monkeypatch.setattr(main,'get_web_schedule',Mock(side_effect=AssertionError('not needed')))
    assert main.fetch_mgsu('2026-11-09','2026-11-15') is result

def test_web_selects_specialist_not_bachelor():
    groups=[{'id':'wrong','university_id':'mgsu','program_track':'bachelor','group_name':'ИПГС 2-64'},
            {'id':'right','university_id':'mgsu','program_track':'specialist','group_name':'ИПГС спец 2-64'}]
    assert mgsu_web.select_group(groups)=='right'

def test_web_real_future_dates_and_subgroup_filter():
    import json
    fixture=Path(__file__).parent/'fixtures/mgsu_web_2026-11-10.json'
    payload=json.loads(fixture.read_text(encoding='utf-8'))
    payload['lessons'].append({'subject':'лаб.Строительные материалы- 2 п/г'})
    rows=mgsu_web.parse_day(payload,'2026-11-10')
    assert len(rows)==4 and rows[0]['subgroup']==1
    assert all(x['date']=='2026-11-10' for x in rows)

def test_web_missing_day_is_not_a_free_day():
    with pytest.raises(SourceError):mgsu_web.parse_day({'date':'2026-11-10','has_schedule':False,'lessons':[]},'2026-11-10')

def test_web_receives_each_date_and_marks_limited_coverage(monkeypatch):
    calls=[]
    def response(method,params=None):
        calls.append((method,params))
        if method=='groups':return [{'id':'specialist-id','university_id':'mgsu','program_track':'specialist','group_name':'ИПГС спец 2-64'}]
        day=params['date']
        return dict(date=day,has_schedule=True,week_parity='odd',lessons=[] if day>='2026-11-14' else [dict(subject='пр.Занятие',time='10:00',time_end='11:20',week_parity='odd')])
    monkeypatch.setattr(mgsu_web,'_json',response)
    batch=mgsu_web.get_web_schedule('2026-11-09','2026-11-15',today=date(2026,11,7))
    assert len(calls)==8 and len(batch.events)==5
    assert all(params['group_id']=='specialist-id' for method,params in calls if method=='schedule')
    assert batch.coverage_from=='2026-11-09' and batch.coverage_to=='2026-11-15' and batch.limited

def test_auto_uses_web_when_pdf_cannot_be_read(monkeypatch):
    monkeypatch.setenv('MGSU_SOURCE','auto')
    monkeypatch.setattr(main,'get_pdf_schedule',Mock(side_effect=SourceError('не прочитан PDF')))
    batch=sources.ScheduleBatch([], '2026-11-09','2026-11-15',provider='mgsu_web',limited=True)
    monkeypatch.setattr(main,'get_web_schedule',lambda *a:batch)
    assert main.fetch_mgsu('2026-11-09','2026-11-15') is batch

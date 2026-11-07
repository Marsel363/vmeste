const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const context=vm.createContext({document:{getElementById:()=>({})},navigator:{onLine:true},Intl,Date,console});
vm.runInContext(source.slice(0,source.indexOf("app.addEventListener('click'")),context);
function run(s){return vm.runInContext(s,context)}
for(const [raw,short] of [['ЛК','ЛК'],['Лекция','ЛК'],['ПЗ','ПЗ'],['ПР','ПЗ'],['Практика','ПЗ'],['ЛР','ЛАБА'],['Лабораторная','ЛАБА'],['Лаборатор.','ЛАБА'],['ЛАБА','ЛАБА'],['Практика · с 6 недели','ПЗ'],['КОП','КОП'],['','ТИП?']]){
 assert.equal(run(`lessonTypeInfo(${JSON.stringify(raw)}).label`),short);
}
assert.match(run("lessonBadge({lesson_type:'ЛР'})"),/class="lesson-type-badge lab"/);
assert.equal(run("lessonMeta({lesson_type:'Практика · с 6 недели',room:'СОК'})"),'с 6 недели · СОК');
assert.match(run("lessonTypeOptions('Лекция')"),/value="Лекция" selected/);
assert.match(run("lessonTypeOptions('')"),/value="ЛАБА"/);
assert.doesNotMatch(run("lessonBadge({lesson_type:'<script>'})"),/<script>/);
run(`moscowDay=()=> '2026-11-07';state.me={members:[],user:{slot:'mai'}};state.sources={mai:{status:'ok',successful_at:'2026-11-07T10:00:00Z'},mgsu:{status:'ok',successful_at:'2026-11-07T10:00:00Z'}};state.events=[{id:1,slot:'mai',date:'2026-11-09',start:'09:00',end:'10:30',title:'Пара',lesson_type:'ЛК',source:'mai_site'}];`);
assert.equal(run("weekendOpeningDay('2026-11-07',state.events,state.sources)"),'2026-11-09');
assert.equal(run("weekendOpeningDay('2026-11-08',state.events,state.sources)"),'2026-11-09');
assert.equal(run("weekendOpeningDay('2026-11-06',state.events,state.sources)"),'2026-11-06');
assert.equal(run("weekendOpeningDay('2026-11-07',[...state.events,{date:'2026-11-07',slot:'mgsu',cancelled:false}],state.sources)"),'2026-11-07');
assert.equal(run("weekendOpeningDay('2026-11-08',[...state.events,{date:'2026-11-08',slot:'mai',cancelled:false}],state.sources)"),'2026-11-08');
assert.equal(run("weekendOpeningDay('2026-11-07',[...state.events,{date:'2026-11-07',cancelled:true}],state.sources)"),'2026-11-09');
assert.equal(run("weekendOpeningDay('2026-11-07',state.events,{mai:state.sources.mai})"),'2026-11-07');
assert.equal(run("weekendOpeningDay('2026-11-07',state.events,{...state.sources,mgsu:{status:'error',successful_at:'2026-11-06'}})"),'2026-11-07');
assert.equal(run("weekendOpeningDay('2026-11-07',[],state.sources)"),'2026-11-07');
assert.equal(run("weekendOpeningDay('2026-11-07',[{date:'2026-11-09',cancelled:true}],state.sources)"),'2026-11-07');
assert.equal(run("weekendOpeningDay('2026-11-07',state.events,{...state.sources,mgsu:{status:'baseline',successful_at:'2026-11-07'}})"),'2026-11-09');
run("state.day='2026-11-07';state.initialDayPending=true;maybeOpenMonday()");
assert.equal(run('state.day'),'2026-11-09');
assert.match(run('openingDayNote()'),/Открыт ближайший понедельник/);
assert.match(run("eventsHtml('mai','2026-11-09')"),/lesson-type-badge lecture/);
run("selectDay('2026-11-07');maybeOpenMonday()");
assert.equal(run('state.day'),'2026-11-07');
assert.equal(run('openingDayNote()'),'');
run("state.initialDayPending=true;state.day='2026-11-07';state.sources.mgsu.status='unverified';maybeOpenMonday()");
assert.equal(run('state.day'),'2026-11-07');
run("state.sources.mgsu.status='ok';maybeOpenMonday()");
assert.equal(run('state.day'),'2026-11-09');
assert.match(source,/Тип занятия<select name="lesson_type">/);
console.log('Lesson types and weekend opening: passed');

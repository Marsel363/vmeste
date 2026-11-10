const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const context=vm.createContext({document:{getElementById:()=>({})},navigator:{onLine:true},window:{matchMedia:()=>({matches:false})},location:{hostname:'vmeste.example',protocol:'https:'},Intl,Date,console});
vm.runInContext(source.slice(0,source.indexOf("app.addEventListener('click'")),context);
const run=s=>vm.runInContext(s,context),slots=()=>JSON.parse(run('JSON.stringify(overlapSlots(state.day))'));
run(`moscowDay=()=> '2026-11-10';moscowTime=()=> '11:46';state.day='2026-11-10';state.me={authenticated:true,members:[{name:'Марсель',slot:'mai'},{name:'Аделина',slot:'mgsu'}],user:{id:1,name:'Марсель',slot:'mai'}};state.tasksComplete=true;state.taskCoverage={from:'2026-11-01',to:'2026-12-20'};state.sources={mai:{status:'ok',reliable:true,loaded:true,count:93,successful_at:'2026-11-10T07:26:57Z',coverage_from:'2026-11-01',coverage_to:'2026-12-20'},mgsu:{status:'baseline',reliable:false,loaded:true,count:108,successful_at:'2026-11-10T07:27:20Z',coverage_from:'2026-11-02',coverage_to:'2026-12-20',message:'СЛУЖЕБНЫЕ_ДАННЫЕ'}};state.events=[{id:1,slot:'mgsu',date:state.day,start:'10:00',end:'11:20',title:'Математика'},{id:2,slot:'mgsu',date:state.day,start:'13:00',end:'14:20',title:'Информатика'}];`);
assert.equal(run("hasReliableSchedule('mgsu')"),false);
assert.equal(run("hasStudyCoverage('mgsu',state.day)"),true);
assert.equal(run("status('mgsu').label"),'Свободен');
assert.doesNotMatch(run('compareHtml()'),/Нет данных|Недостаточно данных/);
assert.match(run('compareHtml()'),/>Пара</);
assert.deepEqual(slots(),[[480,600],[680,780],[860,1320]]);
for(const view of ['today','week','compare','tasks']){run(`state.view='${view}';renderMain()`);assert.doesNotMatch(run('app.innerHTML'),/СЛУЖЕБНЫЕ_ДАННЫЕ|Нет данных/)}
run("state.view='settings';renderMain()");assert.match(run('app.innerHTML'),/СЛУЖЕБНЫЕ_ДАННЫЕ/);
for(const day of ['2026-11-07','2026-11-08','2026-11-14','2026-11-15']){
 run(`moscowDay=()=> '${day}';state.day='${day}'`);
 assert.match(run("eventsHtml('mgsu',state.day)"),/Занятий нет/);
 assert.equal(run("status('mgsu').label"),'Свободен');
 assert.deepEqual(slots(),[[480,1320]]);
 for(const view of ['today','week','compare','tasks']){run(`state.view='${view}';renderMain()`);assert.doesNotMatch(run('app.innerHTML'),/Нет данных|не определено|недостаточно данных/)}
}
run("state.day='2026-11-15';state.tasks=[{slot:'mgsu',date:state.day,start:'11:00',end:'12:30',busy:true,done:false,title:'Врач'}]");
assert.equal(run("status('mgsu').label"),'Занят');assert.deepEqual(slots(),[[480,660],[750,1320]]);
run("state.tasks=[];state.sources.mgsu.status='error';state.sources.mgsu.reliable=false");assert.equal(run("status('mgsu').label"),'Свободен'); // saved grid survived a failed recheck
run('delete state.sources.mgsu.loaded');assert.equal(run("hasLoadedSchedule('mgsu')"),true); // previous cached metadata
run("state.sources.mgsu.successful_at=null");assert.equal(run("hasLoadedSchedule('mgsu')"),false);assert.equal(run("status('mgsu').kind"),'unknown');
run("state.sources.mgsu.successful_at='2026-11-10T07:27:20Z';state.sources.mgsu.loaded=true");
assert.equal(run("hasLoadedSchedule('mgsu','2026-12-21')"),false);
run('state.tasksComplete=false');assert.equal(slots(),null); // a missing task response cannot erase personal plans
run('state.tasksComplete=true;state.online=false');assert.equal(slots(),null);
console.log('Saved MGSU grid: free gaps, all views, weekends, shared windows, personal plans and honest source freshness passed');

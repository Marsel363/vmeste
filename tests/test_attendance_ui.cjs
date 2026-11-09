const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const context=vm.createContext({document:{getElementById:()=>({})},navigator:{onLine:true},window:{matchMedia:()=>({matches:false})},location:{hostname:'vmeste.example',protocol:'https:'},Intl,Date,console});
vm.runInContext(source.slice(0,source.indexOf("app.addEventListener('click'")),context);
const run=s=>vm.runInContext(s,context),slots=()=>JSON.parse(run('JSON.stringify(overlapSlots(state.day))'));
run(`moscowDay=()=> '2026-11-09';moscowTime=()=> '09:01';state.me={authenticated:true,members:[],user:{id:1,name:'Имя',slot:'mai'}};state.day='2026-11-09';state.tasksComplete=true;state.taskCoverage={from:'2026-11-01',to:'2026-11-30'};state.attendanceComplete=true;state.attendanceCoverage={from:'2026-11-01',to:'2026-11-30'};state.sources=Object.fromEntries(['mai','mgsu'].map(s=>[s,{status:'ok',reliable:true,successful_at:'now',coverage_from:'2026-11-01',coverage_to:'2026-11-30'}]));state.events=[{id:1,slot:'mai',date:state.day,start:'10:00',end:'11:20',title:'Физика',can_mark_attendance:true,attendance_scope:'abc',lesson_not_going:false,not_going:false}];`);
assert.equal(run("status('mai').title"),'Пара через 59 минут');
run("moscowTime=()=> '09:00'");assert.equal(run("upcomingLesson('mai')"),null);
for(const [clock,label] of [['09:39','Пара через 21 минуту'],['09:38','Пара через 22 минуты'],['09:49','Пара через 11 минут'],['09:59','Пара через 1 минуту']]){run(`moscowTime=()=> '${clock}'`);assert.equal(run("status('mai').title"),label)}
run("moscowTime=()=> '10:00'");assert.equal(run("status('mai').label"),'На паре');assert.equal(run("upcomingLesson('mai')"),null);
run("moscowTime=()=> '09:30';state.events[0].cancelled=true");assert.equal(run("upcomingLesson('mai')"),null);
run("state.events[0].cancelled=false;state.events[0].not_going=true");assert.equal(run("upcomingLesson('mai')"),null);assert.equal(run('isCurrentLesson(state.events[0],state.day,"10:15")'),false);
assert.match(run("eventsHtml('mai',state.day)"),/Не иду/);assert.doesNotMatch(run("eventsHtml('mai',state.day)"),/cancelled/);assert.equal(run("forDay('mai',state.day).length"),1);
assert.deepEqual(slots(),[[480,1320]]);
run("state.tasks=[{slot:'mai',date:state.day,start:'10:00',end:'11:00',busy:true,done:false,title:'Дело'}]");assert.deepEqual(slots(),[[480,600],[660,1320]]);
run("state.attendanceDays=[{owner_id:1,slot:'mai',date:state.day,not_going:true}]");assert.match(run('dayPicker()'),/attendance-dot/);assert.match(run("eventsHtml('mai',state.day)"),/Не иду на учёбу/);
assert.match(run('lessonAttendanceHtml(state.events[0])'),/Не иду на эту пару/);assert.doesNotMatch(run('lessonAttendanceHtml({...state.events[0],slot:"mgsu"})'),/data-action/);
assert.match(run('lessonAttendanceHtml({...state.events[0],day_not_going:true})'),/disabled/);
run("state.sources.mai.reliable=false");assert.equal(run("hasStudyCoverage('mai',state.day)"),true);run('state.online=false');assert.equal(run("hasStudyCoverage('mai',state.day)"),false);assert.equal(slots(),null);
run("state.online=true;state.attendanceComplete=false");assert.equal(run("hasStudyCoverage('mai',state.day)"),false);
run("state.attendanceComplete=true;state.events[0].not_going=false;state.tasks=[]");assert.equal(run("status('mai').title"),'Пара через 30 минут'); // known next class even if source freshness is uncertain
run("state.events.push({...state.events[0],id:2,start:'09:00',end:'09:45'});");assert.equal(run("upcomingLesson('mai')"),null);
assert.equal(run("upcomingLesson('mai','2026-11-10')"),null);
assert.match(source,/pointercancel/);assert.match(source,/Math.hypot/);assert.match(source,/heldDay===day.dataset.day/);
console.log('Attendance ownership, personal/day marks, busy tasks, countdown boundaries, Russian plurals and offline coverage passed');

'use strict';
const app=document.getElementById('app');
const state={me:null,events:[],sources:{},academicCalendars:{},view:'today',day:moscowDay(),authMode:'create',showLogin:false,loading:false,online:navigator.onLine,modal:null,interval:null,ws:null,refreshing:false,initialDayPending:true,autoOpenedMonday:null};
const dayNames=['Вс','Пн','Вт','Ср','Чт','Пт','Сб'];
const heads=['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pad=n=>String(n).padStart(2,'0');
function moscowDay(){const a=Object.fromEntries(new Intl.DateTimeFormat('en-US',{timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date()).map(x=>[x.type,x.value]));return `${a.year}-${a.month}-${a.day}`}
function moscowTime(){return new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date())}
const mins=time=>{const [h,m]=time.split(':').map(Number);return h*60+m};
const stamp=time=>`${pad(Math.floor(time/60))}:${pad(time%60)}`;
const parseDay=s=>new Date(`${s}T12:00:00Z`);
function shift(day,delta){const d=parseDay(day);d.setUTCDate(d.getUTCDate()+delta);return d.toISOString().slice(0,10)}
function fmtDate(day,opts){return new Intl.DateTimeFormat('ru-RU',{...opts,timeZone:'UTC'}).format(parseDay(day))}
function weekStart(day){const d=parseDay(day);return shift(day,-((d.getUTCDay()+6)%7))}
function toast(msg){const node=document.getElementById('toast');node.textContent=msg;node.classList.add('visible');clearTimeout(toast.timer);toast.timer=setTimeout(()=>node.classList.remove('visible'),3400)}
async function api(path,method='GET',data){const params={method,credentials:'same-origin',headers:{}};if(data!==undefined){params.headers['Content-Type']='application/json';params.headers['X-Vmeste-Client']='pwa';params.body=JSON.stringify(data)}else if(method!=='GET'){params.headers['Content-Type']='application/json';params.headers['X-Vmeste-Client']='pwa';params.body='{}'}
 const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),15000);params.signal=controller.signal;let response;try{response=await fetch('/api/'+path,params)}catch(err){throw new Error('Сервер пока не отвечает. После простоя он может запускаться около минуты — повторите попытку.')}finally{clearTimeout(timer)}
 let result;try{result=await response.json()}catch{throw new Error('Некорректный ответ сервера')}
 if(!response.ok){let detail=result.detail||'Ошибка';if(Array.isArray(detail))detail=detail.map(x=>x.msg).join(', ');throw new Error(detail)}return result;
}
function saveCache(){if(state.me?.authenticated){const s=JSON.stringify({events:state.events,sources:state.sources,academic_calendars:state.academicCalendars,uid:state.me.user.id,time:Date.now()});try{localStorage.setItem('vmeste-schedule',s)}catch{}}}
function readCache(){try{const x=JSON.parse(localStorage.getItem('vmeste-schedule'));if(x.uid===state.me.user.id){state.events=x.events||[];state.sources=x.sources||{};state.academicCalendars=x.academic_calendars||{};return true}}catch{}return false}
async function getMe(){try{state.me=await api('me');if(state.me.authenticated){try{localStorage.setItem('vmeste-profile',JSON.stringify(state.me))}catch{}}else{localStorage.removeItem('vmeste-profile')}render();if(state.me.authenticated){await loadSchedule();connectLive();}}catch{try{const previous=JSON.parse(localStorage.getItem('vmeste-profile'));if(previous?.authenticated&&previous?.user){state.me=previous;state.online=false;readCache();maybeOpenMonday();render();return}}catch{}app.innerHTML='<div class="loading-screen"><div class="logo-symbol">♥</div><p>Сервер недоступен. Проверьте соединение.</p><button class="secondary-btn" onclick="location.reload()">Повторить</button></div>'}}
async function loadSchedule(quiet=false){if(!state.me?.authenticated)return;try{
 let result=await api(`schedule?date_from=${shift(state.day,-50)}&date_to=${shift(state.day,110)}`);
 state.events=result.events;state.sources=result.sources;state.academicCalendars=result.academic_calendars||{};state.online=true;maybeOpenMonday();saveCache();render();
 }catch(e){state.online=false;if(readCache()){maybeOpenMonday();render();if(!quiet)toast('Офлайн: показана сохранённая копия')}else if(!quiet)toast(e.message)}}
function connectLive(){if(!state.me?.authenticated)return;try{state.ws?.close();const ws=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/api/live`);state.ws=ws;ws.onmessage=()=>loadSchedule(true);ws.onclose=()=>{if(state.me?.authenticated)setTimeout(()=>{if(state.ws===ws)connectLive()},7500)}}catch{}}
function switchView(name){state.view=name;render();window.scrollTo({top:0,behavior:'smooth'})}
function navigateDay(delta){selectDay(shift(state.day,delta));loadSchedule(true)}
function isBusy(e){return !e.cancelled}
function forDay(slot,day){return state.events.filter(e=>e.slot===slot&&e.date===day).sort((a,b)=>a.start.localeCompare(b.start))}
function isCurrentLesson(e,day=moscowDay(),clock=moscowTime()){
 return e.date===day&&!e.cancelled&&!e.conflict&&mins(e.start)<=mins(clock)&&mins(clock)<mins(e.end);
}
function lessonNoteHtml(e){return e.note?`<div class="lesson-note"><span aria-hidden="true">✎</span> ${esc(e.note)}</div>`:'';}
function isValidCalendarDay(day){
 if(!/^20\d{2}-\d{2}-\d{2}$/.test(day))return false;
 const d=parseDay(day);return Number.isFinite(d.getTime())&&d.toISOString().slice(0,10)===day;
}
function academicWeek(day){
 const c=state.academicCalendars?.mgsu;
 if(!c||!isValidCalendarDay(day)||day<c.term_start||day>c.term_end)return null;
 const week=Math.floor((parseDay(day)-parseDay(c.week_one_monday))/(7*86400000))+1;
 if(!Number.isInteger(week)||week<1||week>c.weeks)return null;
 return {number:week,parity:week%2?'нечётная':'чётная',label:c.label};
}
function academicWeekHtml(day){const w=academicWeek(day);return w?`<span class="academic-week ${w.number%2?'odd':'even'}">${esc(w.label)} · <strong>${w.number}-я учебная неделя</strong> · ${w.parity}</span>`:'<span class="academic-week unknown">Учебная неделя не подтверждена для этой даты</span>';}
function calendarNavigationHtml(){return `<div class="calendar-navigation"><label class="calendar-date-label" for="schedule-date">Перейти к дате<input id="schedule-date" type="date" min="2000-01-01" max="2099-12-31" value="${esc(state.day)}" aria-label="Дата расписания"></label><div class="calendar-shortcuts"><button class="secondary-btn" data-action="go-today">Сегодня</button><button class="secondary-btn" data-action="go-tomorrow">Завтра</button><button class="secondary-btn" data-action="go-next-class">Ближайшие пары</button></div></div><div class="academic-week-row">${academicWeekHtml(state.day)}</div>`;}
function nextStudyDay(events,today=moscowDay(),clock=moscowTime()){
 return events.filter(e=>!e.cancelled&&!e.conflict&&e.date>=today&&(e.date!==today||mins(e.end)>mins(clock))).map(e=>e.date).sort()[0]||null;
}
async function goToNextStudyDay(){
 const today=moscowDay();
 try{
  const result=await api(`schedule?date_from=${today}&date_to=${shift(today,120)}`);
  const day=nextStudyDay(result.events,today);
  if(!day){toast('В загруженном расписании ближайших пар пока нет');return;}
  selectDay(day);await loadSchedule(true);
 }catch{
  const day=nextStudyDay(state.events,today);
  if(day){selectDay(day);render();toast('Открыты ближайшие пары из сохранённых данных');}
  else toast('Не удалось найти ближайшие пары. Проверьте соединение и загрузку расписаний');
 }
}

function lessonTypeInfo(value){
 const raw=String(value||'').trim();
 const type=raw.split(/\s*·\s*/)[0].trim().toUpperCase();
 if(['ПЗ','ПР','ПРАКТИКА','ПРАКТИЧЕСКОЕ ЗАНЯТИЕ','ПР.'].includes(type))return {label:'ПЗ',kind:'practice',title:'Практическое занятие'};
 if(['ЛК','ЛЕКЦИЯ','Л.'].includes(type))return {label:'ЛК',kind:'lecture',title:'Лекция'};
 if(['ЛАБА','ЛР','ЛАБ','ЛАБ.','ЛАБОРАТОР.','ЛАБОРАТОРНАЯ','ЛАБОРАТОРНАЯ РАБОТА'].includes(type))return {label:'ЛАБА',kind:'lab',title:'Лабораторная работа'};
 return {label:raw?raw.split(/\s*·\s*/)[0]:'ТИП?',kind:'other',title:raw||'Тип занятия не указан — можно выбрать при редактировании'};
}
function lessonBadge(e){const type=lessonTypeInfo(e.lesson_type);return `<span class="lesson-type-badge ${type.kind}" title="${esc(type.title)}">${esc(type.label)}</span>`;}
function lessonMeta(e){const raw=e.lesson_type||'';const extra=raw.includes('·')?raw.slice(raw.indexOf('·')+1).trim():'';return [extra,e.room].filter(Boolean).join(' · ');}
function lessonTypeOptions(value){
 const options=[['','Не указан'],['ПЗ','ПЗ — практическое занятие'],['ЛК','ЛК — лекция'],['ЛАБА','ЛАБА — лабораторная работа'],['КОП','КОП'],['КРП','КРП'],['СЕМ','Семинар'],['ЭКЗ','Экзамен'],['ЗАЧ','Зачёт']];
 if(value&&!options.some(([v])=>v===value))options.unshift([value,`${lessonTypeInfo(value).label} — ${value} (из расписания)`]);
 return options.map(([v,label])=>`<option value="${esc(v)}" ${v===value?'selected':''}>${esc(label)}</option>`).join('');
}
function weekendOpeningDay(today,events,sources){
 const weekday=parseDay(today).getUTCDay();
 if(weekday!==0&&weekday!==6)return today;
 if(events.some(e=>e.date===today&&isBusy(e)))return today;
 // Wait for both imports. A missing response must not be treated as an empty
 // weekend. Choosing Monday changes navigation, not today's availability.
 if(!['mai','mgsu'].every(slot=>sources[slot]?.successful_at&&['ok','partial','baseline'].includes(sources[slot].status)))return today;
 const monday=shift(today,weekday===6?2:1);
 return events.some(e=>e.date===monday&&isBusy(e))?monday:today;
}
function maybeOpenMonday(){
 if(!state.initialDayPending)return;
 const today=moscowDay();
 if(state.day!==today){state.initialDayPending=false;return;}
 const weekday=parseDay(today).getUTCDay();
 if((weekday!==0&&weekday!==6)||state.events.some(e=>e.date===today&&isBusy(e))){state.initialDayPending=false;return;}
 const day=weekendOpeningDay(today,state.events,state.sources);
 if(day!==today){state.day=day;state.autoOpenedMonday=day;state.initialDayPending=false;}
}
function selectDay(day){state.day=day;state.initialDayPending=false;state.autoOpenedMonday=null;}
function openingDayNote(){return state.autoOpenedMonday===state.day?`<div class="schedule-start-note">Открыт ближайший понедельник — ${esc(fmtDate(state.day,{day:'numeric',month:'long'}))}.<button class="ghost-btn" data-action="go-today">Показать сегодня</button></div>`:'';}

function member(slot){const found=state.me?.members?.find(m=>m.slot===slot);return found?.name|| (slot==='mai'?'Ты':'Она')}
function hasReliableSchedule(slot,day=state.day){const s=state.sources[slot];return Boolean(state.online&&s?.reliable===true&&['ok','partial'].includes(s.status)&&s.successful_at&&s.coverage_from&&s.coverage_to&&s.coverage_from<=day&&day<=s.coverage_to)}
function status(slot){const events=forDay(slot,moscowDay()).filter(isBusy);const clock=mins(moscowTime());
 const current=events.find(e=>mins(e.start)<=clock&&mins(e.end)>clock);
 if(current)return {kind:'busy',label:'На паре',title:current.title,detail:`Закончится в ${current.end}`};
 if(!hasReliableSchedule(slot,moscowDay()))return {kind:'unknown',label:'Нет данных',title:'Свободное время не подтверждено',detail:missingReason(slot,moscowDay())};
 const next=events.find(e=>mins(e.start)>clock);
 if(next)return {kind:'free',label:'Свободен',title:'Сейчас свободен',detail:`Следующая пара в ${next.start} · ${next.title}`};
 return {kind:'free',label:'Свободен',title:'Занятий больше нет',detail:'На сегодня все пары закончились'};
}
function overlapSlots(day){if(!hasReliableSchedule('mai',day)||!hasReliableSchedule('mgsu',day))return null;
 const busy=slot=>forDay(slot,day).filter(isBusy).map(e=>[mins(e.start),mins(e.end)]);
 const all=[...busy('mai'),...busy('mgsu')].sort((a,b)=>a[0]-b[0]);const merged=[];
 for(const [start,end] of all){if(!merged.length||start>merged.at(-1)[1])merged.push([start,end]);else merged.at(-1)[1]=Math.max(merged.at(-1)[1],end)}
 const free=[];let current=8*60;for(const [a,b] of merged){if(a-current>=30)free.push([current,Math.min(a,22*60)]);current=Math.max(current,b)}if(22*60-current>=30)free.push([current,22*60]);return free.filter(([a,b])=>b-a>=30);
}
function dayPicker(){const monday=weekStart(state.day),week=Array.from({length:7},(_,i)=>shift(monday,i));return `${calendarNavigationHtml()}<div class="week-controls"><h3>${esc(fmtDate(monday,{day:'numeric',month:'long'}))} — ${esc(fmtDate(shift(monday,6),{day:'numeric',month:'long'}))}</h3><div class="arrow-controls"><button data-action="week-prev" title="Предыдущая неделя">‹</button><button data-action="go-today" title="Сегодня">•</button><button data-action="week-next" title="Следующая неделя">›</button></div></div><div class="days">${week.map(d=>`<button class="day-chip ${d===state.day?'active':''} ${d===moscowDay()?'today':''} ${!state.events.some(e=>e.date===d)?'no-lessons':''}" data-day="${d}"><span>${dayNames[parseDay(d).getUTCDay()]}</span><strong>${parseDay(d).getUTCDate()}</strong><i></i></button>`).join('')}</div>`}
function sourceSummary(slot){const x=state.sources[slot];if(!x)return 'Не подключено';if(x.status==='baseline')return 'Базовое расписание из сохранённого PDF';if(x.status==='partial')return x.message?.includes('raspisanie-mgsu.ru')?'Загружен ограниченный период':'Загружена только одна неделя';if(x.status==='empty')return 'Занятия не получены';if(x.status==='unverified')return 'Нужна повторная проверка';if(x.status==='ok')return 'Загружено';if(x.status==='error')return 'Ошибка синхронизации';return 'Не проверено'}
function missingReason(slot,day){const s=state.sources[slot];if(!state.online)return 'Показана сохранённая копия. Для проверки нужна связь с сервером.';if(!s||s.status==='never')return 'Импорт ещё не выполнен';if(['error','empty','unverified','baseline'].includes(s.status))return s.message||'Не удалось проверить расписание';if(s.coverage_from&&s.coverage_to&&(day<s.coverage_from||day>s.coverage_to))return `Источник дал данные только за ${fmtDate(s.coverage_from,{day:'numeric',month:'long'})} — ${fmtDate(s.coverage_to,{day:'numeric',month:'long'})}. Эта дата не подтверждена.`;return 'Нужна свежая проверка источника в настройках';}
function eventsHtml(slot,day){const ev=forDay(slot,day);if(!ev.length)return `<div class="empty"><strong>${hasReliableSchedule(slot,day)?'Занятий в источнике нет':'Нет данных на эту дату'}</strong>${hasReliableSchedule(slot,day)?'По последней проверке расписания':esc(missingReason(slot,day))}</div>`;
 return `<div class="lesson-list">${ev.map(e=>`<article class="lesson ${slot==='mgsu'?'her':''} ${e.cancelled?'cancelled':''} ${isCurrentLesson(e)?'is-current':''}" ${isCurrentLesson(e)?'aria-current="true"':''} data-edit="${e.id}" role="button" tabindex="0" aria-label="Изменить ${esc(e.title)}"><div class="lesson-time">${esc(e.start)}<small>${esc(e.end)}</small></div><div><div class="lesson-title-row">${lessonBadge(e)}<div class="lesson-name">${esc(e.title)}</div></div><div class="lesson-meta">${esc(lessonMeta(e))}${e.teacher?'<div>'+esc(e.teacher)+'</div>':''}</div>${isCurrentLesson(e)?'<span class="current-lesson-label">Сейчас идёт</span>':''}${lessonNoteHtml(e)}${e.cancelled?'<span class="cancel-label">Отменена</span>':''}${e.conflict?'<span class="conflict-label">Изменилось в источнике</span>':''}</div></article>`).join('')}</div>`}
function statusCard(slot){const x=status(slot);const her=slot==='mgsu';return `<div class="status-card card ${her?'her':''}"><div class="person-top"><div class="person-label"><span class="avatar">${her?'♡':'◉'}</span><div><strong>${esc(member(slot))}</strong><div class="uni">${her?'МГСУ · ИПГС 2-64':'МАИ · М3О-237БВ-25'}</div></div></div><span class="status-tag ${x.kind==='busy'?'busy':x.kind==='unknown'?'unknown':''}">${esc(x.label)}</span></div><div class="current-title">${esc(x.title)}</div><div class="status-detail">${esc(x.detail)}</div></div>`}
function prettyChecked(x){if(!x)return 'Ещё не было';try{return new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}).format(new Date(x))}catch{return x}}
function sourcesHtml(){return `<div class="heading-row"><h2>Источники расписаний</h2><button class="secondary-btn" data-action="sync">↻ Обновить</button></div><div class="sources-list">${['mai','mgsu'].map(slot=>{const s=state.sources[slot]||{};return `<div class="card source-card"><h3>${slot==='mai'?'МАИ · М3О-237БВ-25':'МГСУ · ИПГС 2-64, подгр. 1'}</h3><p>${slot==='mai'?'Сайт кафедры 406 МАИ':'Официальный PDF МГСУ · резерв: raspisanie-mgsu.ru и Raspika'}</p><div class="source-status ${['error','empty'].includes(s.status)?'error':s.status!=='ok'?'unknown':''}">${esc(sourceSummary(slot))}</div><p>Успешно: ${esc(prettyChecked(s.successful_at))}.<br>Последняя попытка: ${esc(prettyChecked(s.checked_at))}.</p><p>${esc(s.message)}</p><p>Занятий в последней загрузке: ${esc(s.count||0)}.${s.coverage_from&&s.coverage_to?`<br>Сохранённый период: ${esc(s.coverage_from)} — ${esc(s.coverage_to)}.`:''}</p></div>`}).join('')}</div><p class="small-muted" style="margin-top:14px">Дополнительный источник МГСУ: <a href="https://raspika.com/raspisanie/mgsu/%D0%98%D0%9F%D0%93%D0%A1-2-64-so" target="_blank" rel="noopener noreferrer">источник Raspika</a>. Импортированные пары обновляются без удаления ваших исправлений. <a href="https://mgsu.ru/student/Raspisanie_zanyatii_i_ekzamenov/fayly-raspisaniya-dlya-skachivaniya/" target="_blank" rel="noopener noreferrer">Официальные файлы МГСУ</a>.</p>`}
function inviteBanner(){if(!state.me.invite_code)return '';return `<div class="join-banner"><div><strong>Пригласи второго человека ♡</strong><p>Создай ей аккаунт по коду — тогда расписания будут общими.</p><div class="invite-code" style="margin-top:9px">${esc(state.me.invite_code)}</div></div><button class="secondary-btn" data-action="copy-invite">Копировать</button></div>`}
function todayHtml(){const day=state.day;const today=day===moscowDay();const slots=overlapSlots(day);let banner='';
 if(slots===null)banner='<strong>Общее время определим после загрузки обоих расписаний.</strong><p>Пока один из источников недоступен, мы не считаем весь день свободным.</p>';
 else if(!slots.length)banner='<strong>Общих окон от 30 минут нет.</strong><p>Попробуйте соседний день или отмените пару, если её перенесли.</p>';
 else{const now=mins(moscowTime());let next=today?slots.find(([a,b])=>b>now):slots[0];banner=next?`<strong>Вы оба свободны ${today&&mins(moscowTime())>=next[0]?'сейчас':`с ${stamp(next[0])} до ${stamp(next[1])}`} ♡</strong><p>${slots.length} ${slots.length===1?'общее окно':'общих окна'} за день. Можно выбрать время для встречи или звонка.</p>`:'<strong>Сегодня совместных окон больше нет</strong><p>Посмотрите завтра.</p>'}
 return `${inviteBanner()}${openingDayNote()}<div class="heading-row"><h2>Сейчас</h2><span class="small-muted">Время московское · ${esc(moscowTime())}</span></div><div class="statuses">${statusCard('mai')}${statusCard('mgsu')}</div><div class="heading-row"><h2>Расписание</h2><button class="secondary-btn" data-action="add">+ Добавить пару</button></div>${dayPicker()}<div class="lesson-columns" style="margin-top:19px">${['mai','mgsu'].map(slot=>`<section><div class="column-title"><strong>${esc(member(slot))}</strong><span>${forDay(slot,day).filter(isBusy).length} пар</span></div>${eventsHtml(slot,day)}</section>`).join('')}</div><div class="heading-row"><h2>Когда вы оба свободны${today?'':` · ${esc(fmtDate(day,{day:'numeric',month:'long'}))}`}</h2></div><div class="callout"><div class="callout-icon">♡</div><div>${banner}</div></div>`}
function weekHtml(){let monday=weekStart(state.day);return `${inviteBanner()}<div class="heading-row"><h2>Неделя</h2><button class="secondary-btn" data-action="add">+ Добавить пару</button></div>${dayPicker()}${Array.from({length:7},(_,i)=>{const d=shift(monday,i);return `<section><div class="heading-row"><h2>${esc(fmtDate(d,{weekday:'long',day:'numeric',month:'long'}))}</h2><button class="ghost-btn" data-day="${d}" data-view="today">Открыть</button></div><div class="lesson-columns">${['mai','mgsu'].map(slot=>`<div>${eventsHtml(slot,d)}</div>`).join('')}</div></section>`}).join('')}`}
function compareHtml(){let windows=overlapSlots(state.day);return `<div class="heading-row"><h2>Сравнение расписаний</h2><span class="small-muted">Занятия обеих групп</span></div>${dayPicker()}${windows===null?'<div class="alert">Для достоверного сравнения требуется загрузить оба расписания.</div>':''}<div class="card compare-board" style="margin-top:17px"><div class="compare-head"><span>Время</span><strong>${esc(member('mai'))}</strong><strong>${esc(member('mgsu'))}</strong></div>${Array.from({length:28},(_,i)=>{const t=8*60+i*30;return `<div class="compare-row"><span class="compare-time">${stamp(t)}</span>${['mai','mgsu'].map(slot=>{const unknown=!hasReliableSchedule(slot,state.day);const occupied=forDay(slot,state.day).some(e=>isBusy(e)&&mins(e.start)<t+30&&mins(e.end)>t);return `<span class="compare-cell ${occupied?`busy ${slot}`:unknown?'unknown':''}">${occupied?'Пара':unknown?'Нет данных':'Свободен'}</span>`}).join('')}</div>`}).join('')}</div><div class="heading-row"><h2>Общие окна от 30 минут</h2></div>${windows?.length?`<div class="availability-list">${windows.map(([a,b])=>`<span class="free-pill">${stamp(a)} — ${stamp(b)}</span>`).join('')}</div>`:`<div class="empty">${windows===null?'Сначала загрузим оба расписания':'На эту дату общих окон нет'}</div>`}`}
function standaloneApp(){return Boolean(navigator.standalone||window.matchMedia?.('(display-mode: standalone)').matches);}
function phoneAppHtml(){
 const local=['localhost','127.0.0.1','[::1]'].includes(location.hostname);
 if(standaloneApp())return '<div class="card card-pad install-card"><h3>Приложение на iPhone</h3><p>«Вместе» уже открыто в отдельном окне приложения.</p></div>';
 if(local)return '<div class="card card-pad install-card"><h3>Приложение на iPhone</h3><p>Сейчас это локальный запуск на компьютере. После размещения появится общая HTTPS-ссылка для обоих телефонов.</p></div>';
 if(location.protocol!=='https:')return '<div class="card card-pad install-card"><h3>Приложение на iPhone</h3><p>Для установки откройте защищённую HTTPS-ссылку приложения.</p></div>';
 return '<div class="card card-pad install-card"><h3>Установить на iPhone</h3><ol><li>Откройте эту ссылку в Safari.</li><li>Нажмите «Поделиться» → «На экран Домой».</li><li>Оставьте «Открывать как веб-приложение» включённым, если такой переключатель есть, и нажмите «Добавить».</li></ol><p>После установки открывайте «Вместе» значком на главном экране. При необходимости войдите прежним аккаунтом.</p><button class="secondary-btn" data-action="copy-app-link">Скопировать ссылку</button></div>';
}

function settingsHtml(){return `${phoneAppHtml()}<div class="heading-row"><h2>Настройки синхронизации</h2></div><div class="card card-pad"><p style="font-size:13px;line-height:1.8;margin:0 0 12px">Автоматическая проверка вузов — раз в 24 часа, а изменения, сделанные вами, появляются у обоих без ожидания обновления сайта.</p><button class="primary-btn" data-action="sync">↻ Проверить сейчас</button></div>${sourcesHtml()}<div class="heading-row"><h2>Аккаунт</h2></div><div class="card card-pad"><p style="font-size:13px">${esc(state.me.user.name)} · ${esc(state.me.user.username)}</p><button class="secondary-btn" data-action="logout">Выйти из аккаунта</button></div>`}
function renderMain(){const active=name=>state.view===name?'active':'';app.innerHTML=`<div class="layout"><aside class="sidebar"><div class="brand"><span class="logo-symbol">♥</span><span>Вместе<span style="color:var(--coral)">.</span></span></div><div class="side-kicker">НАВИГАЦИЯ</div><nav class="nav"><button class="${active('today')}" data-view="today"><span class="nav-icon">⌂</span>Сегодня</button><button class="${active('week')}" data-view="week"><span class="nav-icon">▦</span>Неделя</button><button class="${active('compare')}" data-view="compare"><span class="nav-icon">♡</span>Сравнение</button><button class="${active('settings')}" data-view="settings"><span class="nav-icon">⚙</span>Настройки</button></nav><div class="side-bottom"><div class="side-user">${esc(state.me.user.name)}</div><div class="small-muted">${esc(state.me.user.slot==='mai'?'МАИ':'МГСУ')}</div><button class="ghost-btn" data-action="logout">Выйти</button></div></aside><main class="main"><header class="topbar"><div><div class="eyebrow">НАШЕ ВРЕМЯ</div><h1>${{today:'Наше расписание',week:'Вся неделя',compare:'Когда мы свободны',settings:'Настройки'}[state.view]}</h1></div><div class="top-right"><span class="date-pill">${esc(fmtDate(moscowDay(),{day:'numeric',month:'long',weekday:'short'}))}</span><button class="icon-btn" title="К сегодняшнему дню" data-action="go-today">◷</button></div></header>${!state.online?'<div class="alert">Нет связи с сервером. Показана сохранённая копия; редактирование может быть недоступно.</div>':''}${Object.values(state.sources).some(s=>['error','empty','unverified'].includes(s.status))?'<div class="alert">Не все расписания удалось обновить. Доступные данные могут быть устаревшими — проверьте раздел «Настройки».</div>':''}${state.view==='today'?todayHtml():state.view==='week'?weekHtml():state.view==='compare'?compareHtml():settingsHtml()}<p class="bottom-copy">Вместе ♡ · Время занятий в часовом поясе Москвы</p></main></div>`;
}
function renderAuth(){app.innerHTML=`<div class="auth-page"><section class="auth-art"><div class="brand"><span class="logo-symbol">♥</span>Вместе.</div><div><div class="eyebrow" style="color:#e3daff">ВМЕСТЕ ДАЖЕ В УЧЕБНЫЕ ДНИ</div><h1>Больше времени<br>друг для друга.</h1><p>Два расписания, одно свободное время. Смотрите, когда у каждого закончится пара, и планируйте встречи.</p></div><div class="small-muted" style="color:#e5dcff">Приложение только для вас двоих.</div></section><section class="auth-form-wrap"><div class="auth-panel"><h2>${state.showLogin?'С возвращением!':'Привет! ♡'}</h2><p class="small-muted">${state.showLogin?'Войдите, чтобы увидеть ваше общее расписание.':'Создайте личный доступ к совместному календарю.'}</p>${!state.showLogin?`<div class="tabs"><button class="${state.authMode==='create'?'active':''}" data-authmode="create">Создать пару</button><button class="${state.authMode==='join'?'active':''}" data-authmode="join">По приглашению</button></div>`:''}<form id="auth-form">${!state.showLogin?`<label class="field">Как тебя называть<input required name="display_name" minlength="1" maxlength="45" placeholder="Имя" autocomplete="nickname"></label>`:''}<label class="field">Логин<input required name="username" minlength="3" maxlength="60" pattern="[A-Za-z0-9_.-]+" placeholder="Например, marsel" autocomplete="username"></label><label class="field">Пароль<input required type="password" name="password" minlength="${state.showLogin?1:10}" placeholder="${state.showLogin?'Твой пароль':'Не менее 10 символов'}" autocomplete="${state.showLogin?'current-password':'new-password'}"></label>${!state.showLogin&&state.authMode==='join'?`<label class="field">Код приглашения<input required name="invite_code" placeholder="Код от партнёра"></label>`:''}<div id="auth-error" class="form-error hidden"></div><button class="primary-btn auth-submit" type="submit">${state.showLogin?'Войти':state.authMode==='join'?'Присоединиться':'Создать наш календарь'}</button></form><div class="form-switch">${state.showLogin?'Нет аккаунта?':'Уже есть аккаунт?'} <button data-action="toggle-login">${state.showLogin?'Зарегистрироваться':'Войти'}</button></div></div></section></div>`}
function render(){if(!state.me?.authenticated)renderAuth();else renderMain();if(state.modal){if(!document.querySelector('.modal-backdrop'))renderModal()}else document.querySelector('.modal-backdrop')?.remove()}
function openEvent(id){const e=state.events.find(e=>e.id===Number(id));if(!e)return;state.modal={type:'edit',event:e};renderModal()}
function openAdd(){state.modal={type:'add'};renderModal()}
function renderModal(){document.querySelector('.modal-backdrop')?.remove();if(!state.modal)return;
 const e=state.modal.event;const editing=!!e;const v={slot:e?.slot||(state.me.user.slot),date:e?.date||state.day,start:e?.start||'13:00',end:e?.end||'14:30',title:e?.title||'',room:e?.room||'',teacher:e?.teacher||'',lesson_type:e?.lesson_type||'',note:e?.note||'',cancelled:e?.cancelled||false};
 const backdrop=document.createElement('div');backdrop.className='modal-backdrop';backdrop.innerHTML=`<section class="modal" role="dialog" aria-modal="true" aria-label="Редактирование пары"><div class="modal-head"><h2>${editing?'Изменить пару':'Добавить пару'}</h2><button data-action="close-modal" aria-label="Закрыть">×</button></div><form id="event-form"><div class="modal-fields"><label class="field wide">Чьё расписание<select name="slot" ${editing?'disabled':''}><option value="mai" ${v.slot==='mai'?'selected':''}>${esc(member('mai'))} · МАИ</option><option value="mgsu" ${v.slot==='mgsu'?'selected':''}>${esc(member('mgsu'))} · МГСУ</option></select></label><label class="field wide">Название дисциплины<input required name="title" maxlength="200" value="${esc(v.title)}" placeholder="Например, Теория инноваций"></label><label class="field wide">Дата<input required type="date" name="date" value="${esc(v.date)}"></label><label class="field">Начало<input required type="time" name="start" value="${esc(v.start)}"></label><label class="field">Конец<input required type="time" name="end" value="${esc(v.end)}"></label><label class="field wide">Тип занятия<select name="lesson_type">${lessonTypeOptions(v.lesson_type)}</select></label><label class="field wide">Заметка к паре<textarea name="note" maxlength="1000" rows="3" placeholder="Например: контрольная, принести чертёж">${esc(v.note)}</textarea><span class="field-help">Видна вам обоим. Сохранится после обновления расписания.</span></label><label class="field">Аудитория<input name="room" value="${esc(v.room)}" maxlength="100" placeholder="Номер"></label><label class="field">Преподаватель<input name="teacher" value="${esc(v.teacher)}" maxlength="160" placeholder="Фамилия"></label></div>${editing?`<label class="cancel-toggle"><input type="checkbox" name="cancelled" ${v.cancelled?'checked':''}>Пара отменена (не считать занятым)</label>`:''}<div id="event-error" class="form-error hidden"></div><div class="modal-actions">${editing&&e.source==='manual'?'<button class="danger-btn" type="button" data-action="delete-event">Удалить</button>':'<span></span>'}<button class="primary-btn" type="submit">Сохранить изменения</button></div></form></section>`;
 backdrop.addEventListener('click',evt=>{if(evt.target===backdrop){state.modal=null;render()}});document.body.appendChild(backdrop);
}
async function syncNow(){if(state.refreshing)return;state.refreshing=true;toast('Проверяем оба источника…');try{const r=await api('sync','POST',{});await loadSchedule(true);const failed=Object.values(r).filter(x=>x&&x.ok===false);toast(failed.length?'Не все расписания удалось получить. Подробности — в настройках.':Object.values(r).some(x=>x?.status==='partial')?'Загружены доступные даты. Период и источник указаны в настройках.':'Расписания загружены')}catch(e){toast(e.message)}finally{state.refreshing=false}}
async function logOut(){try{await api('logout','POST',{})}catch(e){toast(e.message);return}state.ws?.close();state.me={authenticated:false};state.day=moscowDay();state.initialDayPending=true;state.autoOpenedMonday=null;state.events=[];state.sources={};state.academicCalendars={};localStorage.removeItem('vmeste-schedule');localStorage.removeItem('vmeste-profile');render()}
async function submitAuth(form){const err=document.getElementById('auth-error');err.classList.add('hidden');const f=new FormData(form),p=Object.fromEntries(f.entries());if(!state.showLogin)p.mode=state.authMode;
 const btn=form.querySelector('button[type=submit]');btn.disabled=true;
 try{await api(state.showLogin?'login':'register','POST',p);state.me=await api('me');try{localStorage.setItem('vmeste-profile',JSON.stringify(state.me))}catch{}await loadSchedule(true);connectLive();toast('Добро пожаловать! ♡')}catch(e){err.textContent=e.message;err.classList.remove('hidden')}finally{btn.disabled=false}}
async function submitEvent(form){const err=document.getElementById('event-error');err.classList.add('hidden');const f=new FormData(form),p=Object.fromEntries(f.entries());p.cancelled=Boolean(f.get('cancelled'));
 if(state.modal.type==='edit'){p.slot=state.modal.event.slot;p.revision=state.modal.event.revision}
 if(p.start>=p.end){err.textContent='Время окончания должно быть позже начала';err.classList.remove('hidden');return}
 const button=form.querySelector('button[type=submit]');button.disabled=true;
 try{if(state.modal.type==='edit')await api('events/'+state.modal.event.id,'PUT',p);else await api('events','POST',p);state.modal=null;await loadSchedule(true);toast('Изменения сохранены у вас обоих')}catch(e){err.textContent=e.message;err.classList.remove('hidden')}finally{button.disabled=false}}
async function deleteEvent(){const id=state.modal?.event?.id;if(!id||!confirm('Удалить добавленную пару?'))return;try{await api('events/'+id,'DELETE',{});state.modal=null;await loadSchedule(true);toast('Пара удалена')}catch(e){toast(e.message)}}
app.addEventListener('click',async evt=>{const day=evt.target.closest('[data-day]');if(day){selectDay(day.dataset.day);if(day.dataset.view)state.view=day.dataset.view;loadSchedule(true);return}
 const view=evt.target.closest('[data-view]');if(view){switchView(view.dataset.view);return}
 const auth=evt.target.closest('[data-authmode]');if(auth){state.authMode=auth.dataset.authmode;render();return}
 const edit=evt.target.closest('[data-edit]');if(edit){openEvent(edit.dataset.edit);return}
 const el=evt.target.closest('[data-action]');if(!el)return;
 switch(el.dataset.action){case'copy-app-link':try{await navigator.clipboard.writeText(location.origin+'/');toast('Ссылка скопирована')}catch{toast('Ссылка: '+location.origin+'/')}break;case'week-prev':navigateDay(-7);break;case'week-next':navigateDay(7);break;case'go-today':selectDay(moscowDay());loadSchedule(true);break;case'go-tomorrow':selectDay(shift(moscowDay(),1));loadSchedule(true);break;case'go-next-class':await goToNextStudyDay();break;case'add':openAdd();break;case'sync':await syncNow();break;case'logout':await logOut();break;case'toggle-login':state.showLogin=!state.showLogin;render();break;case'copy-invite':try{await navigator.clipboard.writeText(state.me.invite_code);toast('Код скопирован')}catch{toast('Код: '+state.me.invite_code)}break;}
});
app.addEventListener('change',evt=>{if(evt.target.id==='schedule-date'&&isValidCalendarDay(evt.target.value)){selectDay(evt.target.value);loadSchedule(true)}});
document.addEventListener('click',async evt=>{const el=evt.target.closest('.modal [data-action]');if(!el)return;if(el.dataset.action==='close-modal'){state.modal=null;render()}if(el.dataset.action==='delete-event')await deleteEvent()});
document.addEventListener('submit',async evt=>{if(evt.target.id==='auth-form'){evt.preventDefault();await submitAuth(evt.target)}if(evt.target.id==='event-form'){evt.preventDefault();await submitEvent(evt.target)}});
app.addEventListener('keydown',evt=>{const e=evt.target.closest('[data-edit]');if(e&&(evt.key==='Enter'||evt.key===' ')){evt.preventDefault();openEvent(e.dataset.edit)}});
window.addEventListener('online',()=>{state.online=true;loadSchedule(true)});
window.addEventListener('offline',()=>{state.online=false;render()});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)loadSchedule(true)});
state.interval=setInterval(()=>{if(state.me?.authenticated){if(!document.hidden){if(state.view==='today'||state.view==='compare')render();loadSchedule(true)}}},30000);
if('serviceWorker'in navigator&&(location.protocol==='https:'||['localhost','127.0.0.1'].includes(location.hostname)))navigator.serviceWorker.register('/sw.js').catch(()=>{});
getMe();

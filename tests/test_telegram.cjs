// Run: node tests/test_telegram.cjs
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..');
const adapter=fs.readFileSync(path.join(root,'static/telegram.js'),'utf8');
const appSource=fs.readFileSync(path.join(root,'static/app.js'),'utf8');
function setup(search='',hash='',platform='ios'){
 const events={},scripts=[],calls=[],classes=new Set(),clicks={},app={innerHTML:'',addEventListener(){}};
 const webApp={platform,initDataUnsafe:{user:{id:999999}},ready:()=>calls.push('ready'),expand:()=>calls.push('expand'),
 setHeaderColor:()=>{},setBackgroundColor:()=>{},setBottomBarColor:()=>{},
 enableClosingConfirmation:()=>calls.push('protect'),disableClosingConfirmation:()=>calls.push('unprotect'),
 onEvent:(name,fn)=>events[name]=fn,
 BackButton:{show:()=>calls.push('showBack'),hide:()=>calls.push('hideBack'),onClick:fn=>clicks.back=fn},
 SettingsButton:{show:()=>calls.push('showSettings'),hide:()=>calls.push('hideSettings'),onClick:fn=>clicks.settings=fn}};
 const document={head:{appendChild:s=>scripts.push(s)},documentElement:{classList:{add:c=>classes.add(c)}},
 createElement:()=>({}),getElementById:()=>app,querySelector:()=>null,
 addEventListener:(name,fn)=>{(events[name]??=[]).push(fn)},dispatchEvent:event=>{for(const fn of events[event.type]||[])fn(event)}};
 const context=vm.createContext({document,location:{search,hash,protocol:'https:',hostname:'vmeste.example',origin:'https://vmeste.example'},
 navigator:{onLine:true},Intl,Date,URLSearchParams,CustomEvent:class{constructor(type){this.type=type}},console,setInterval:()=>1,setTimeout,clearTimeout});
 context.window=context;context.addEventListener=()=>{};
 vm.runInContext(adapter,context);
 return {context,webApp,scripts,calls,classes,clicks,app,events,run:code=>vm.runInContext(code,context)};
}
let s=setup();
assert.equal(s.scripts.length,0);assert.equal(s.context.VmesteTelegram.active,false);
s=setup('?telegram=1');
assert.equal(s.scripts.length,1);assert.equal(s.scripts[0].src,'https://telegram.org/js/telegram-web-app.js?64');
s.context.Telegram={WebApp:s.webApp};s.scripts[0].onload();
assert.equal(s.context.VmesteTelegram.active,true);assert(s.classes.has('telegram-app'));
assert(s.calls.includes('ready'));assert(s.calls.includes('expand'));
s.run(appSource.replace(/getMe\(\);\s*$/,''));
assert.equal(s.run('state.showLogin'),true);
assert.equal(s.run('state.me'),null); // Telegram metadata never authenticates an account.
s.run("state.me={authenticated:true,members:[],user:{name:'Имя',username:'name',slot:'mai'}};state.view='week';render()");
assert(s.calls.includes('showBack'));s.clicks.back();
assert.equal(s.run('state.view'),'today');
s.clicks.settings();assert.equal(s.run('state.view'),'settings');
assert.match(s.app.innerHTML,/«Вместе» в Telegram/);
s.run("state.modal={type:'add'};telegramUI()");
assert.equal(s.calls.at(-1),'protect');
s.clicks.back();assert.equal(s.run('state.modal'),null);assert.equal(s.calls.at(-1),'unprotect');
s=setup('', '#tgWebAppPlatform=ios&tgWebAppVersion=8.0');
assert.equal(s.scripts.length,1);
s=setup('?telegram=1','','unknown');s.context.Telegram={WebApp:s.webApp};s.scripts[0].onload();
assert.equal(s.context.VmesteTelegram.active,false);
s=setup('?telegram=1');delete s.webApp.BackButton;delete s.webApp.SettingsButton;delete s.webApp.enableClosingConfirmation;
s.webApp.setHeaderColor=()=>{throw Error('old client')};s.context.Telegram={WebApp:s.webApp};
assert.doesNotThrow(()=>s.scripts[0].onload());
assert.doesNotThrow(()=>s.context.VmesteTelegram.sync({authenticated:true,back:true,editing:true}));
console.log('Telegram launch, native navigation, editor protection, browser fallback and account isolation: passed');

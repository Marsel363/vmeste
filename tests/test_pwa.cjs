const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=path.join(__dirname,'..'),source=fs.readFileSync(path.join(root,'static/app.js'),'utf8');
const context=vm.createContext({document:{getElementById:()=>({})},navigator:{onLine:true,standalone:false},window:{matchMedia:()=>({matches:false})},location:{hostname:'vmeste.example',protocol:'https:'},Intl,Date,console});
vm.runInContext(source.slice(0,source.indexOf("app.addEventListener('click'")),context);
function run(s){return vm.runInContext(s,context)}
assert.match(run('phoneAppHtml()'),/Safari/);
assert.match(run('phoneAppHtml()'),/На экран Домой/);
run("navigator.standalone=true");assert.match(run('phoneAppHtml()'),/уже открыто/);
run("navigator.standalone=false;location.hostname='localhost'");assert.match(run('phoneAppHtml()'),/локальный запуск/);
const manifest=JSON.parse(fs.readFileSync(path.join(root,'static/manifest.webmanifest'),'utf8'));
assert.equal(manifest.display,'standalone');assert.equal(manifest.scope,'/');
assert.match(fs.readFileSync(path.join(root,'static/index.html'),'utf8'),/apple-touch-icon/);
for(const icon of manifest.icons)assert.ok(fs.existsSync(path.join(root,icon.src.slice(1))));
const sw=fs.readFileSync(path.join(root,'static/sw.js'),'utf8');
const handlers={},store=new Map();let fetcher;
const caches={match:async key=>store.get(typeof key==='string'?key:key.url)?.clone(),keys:async()=>[],open:async()=>({put:async(key,response)=>store.set(key,response.clone())})};
const swContext=vm.createContext({self:{location:{origin:'https://vmeste.example'},addEventListener:(name,fn)=>handlers[name]=fn},caches,fetch:(...a)=>fetcher(...a),Response,URL,setTimeout});
vm.runInContext(sw,swContext);
function request(url,mode='navigate',method='GET'){
 const pending=[];let reply;
 handlers.fetch({request:{url:'https://vmeste.example'+url,mode,method},respondWith:r=>reply=r,waitUntil:r=>pending.push(r)});
 return {reply,pending};
}
(async()=>{
 store.set('/',new Response('our cached app'));
 fetcher=async()=>new Response('host loading page');
 let event=request('/');assert.equal(await (await event.reply).text(),'our cached app');await Promise.all(event.pending);
 assert.equal(await store.get('/').clone().text(),'our cached app');
 fetcher=async()=>{throw Error('offline')};event=request('/');assert.equal(await (await event.reply).text(),'our cached app');await Promise.all(event.pending);
 event=request('/api/schedule','cors');assert.equal(event.reply,undefined);
 event=request('/api/events','cors','POST');assert.equal(event.reply,undefined);
 fetcher=async()=>new Response('fresh app',{headers:{'X-Vmeste-App':'1'}});
 event=request('/');await event.reply;await Promise.all(event.pending);assert.equal(await store.get('/').clone().text(),'fresh app');
 store.clear();fetcher=async()=>{throw Error('offline')};event=request('/');assert.equal((await event.reply).status,503);await Promise.all(event.pending);
 console.log('PWA installation and sleeping-host/offline cache: passed');
})().catch(e=>{console.error(e);process.exitCode=1});

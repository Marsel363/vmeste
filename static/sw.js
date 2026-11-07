const CACHE='vmeste-shell-v11';
const SHELL=['/','/static/style.css','/static/app.js','/static/telegram.js','/static/telegram.css','/static/icons/icon-192.png','/static/icons/icon-512.png','/manifest.webmanifest'];
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(SHELL)).then(()=>self.skipWaiting())));
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('vmeste-shell-')&&k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
function appResponse(response){return response.ok&&response.headers.get('X-Vmeste-App')==='1';}
self.addEventListener('fetch',event=>{
 const req=event.request,u=new URL(req.url);
 if(req.method!=='GET'||u.origin!==self.location.origin||u.pathname.startsWith('/api/'))return;
 if(req.mode==='navigate'){
  // A sleeping free host can return its own loading page. Keep our known shell
  // and let the network request wake the server, without caching that page.
  const network=fetch(req).then(async response=>{
   if(appResponse(response)){const cache=await caches.open(CACHE);await cache.put('/',response.clone());return response;}
   return (await caches.match('/'))||response;
  });
  event.waitUntil(network.catch(()=>{}));
  event.respondWith((async()=>{
   const saved=await caches.match('/');
   if(saved){return Promise.race([network.catch(()=>saved),new Promise(resolve=>setTimeout(()=>resolve(saved),2500))]);}
   try{return await network;}
   catch{return new Response('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Вместе</title><body><h1>Вместе</h1><p>Нужна связь с сервером для первого открытия приложения. Повторите попытку через минуту.</p><button onclick="location.reload()">Повторить</button></body></html>',{status:503,headers:{'Content-Type':'text/html; charset=utf-8'}});}
  })());
  return;
 }
 if(!SHELL.includes(u.pathname))return;
 event.respondWith((async()=>{
  const saved=await caches.match(u.pathname);if(saved)return saved;
  const response=await fetch(req);
  if(appResponse(response)){const cache=await caches.open(CACHE);await cache.put(u.pathname,response.clone());}
  return response;
 })());
});

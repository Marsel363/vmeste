'use strict';
// This adapter controls presentation only. Telegram launch data never grants account access.
(() => {
 const launch = new URLSearchParams(location.search);
 const fragment = new URLSearchParams(location.hash.slice(1));
 const requested = launch.get('telegram') === '1' || ['tgWebAppData','tgWebAppVersion','tgWebAppPlatform'].some(key => fragment.has(key));
 let webApp = null, lastUI = {authenticated:false,back:false,editing:false};
 const call = (target,name,...args) => {try{return target?.[name]?.(...args)}catch{return undefined}};
 const emit = name => document.dispatchEvent(new CustomEvent(name));
 const bridge = window.VmesteTelegram = {
  active:false,requested,
  sync(ui){lastUI=ui;if(!webApp)return;
   call(webApp.BackButton,ui.back?'show':'hide');
   call(webApp.SettingsButton,ui.authenticated?'show':'hide');
   call(webApp,ui.editing?'enableClosingConfirmation':'disableClosingConfirmation');
  }
 };
 function colors(){
  call(webApp,'setHeaderColor','#7364df');
  call(webApp,'setBackgroundColor','#f6f7fb');
  call(webApp,'setBottomBarColor','#f6f7fb');
 }
 function initialize(){
  const candidate=window.Telegram?.WebApp;
  if(!candidate || !candidate.platform || candidate.platform==='unknown')return;
  webApp=candidate;bridge.active=true;
  document.documentElement.classList.add('telegram-app');
  colors();call(webApp,'expand');
  call(webApp.BackButton,'onClick',()=>emit('vmeste:telegram-back'));
  call(webApp.SettingsButton,'onClick',()=>emit('vmeste:telegram-settings'));
  call(webApp,'onEvent','themeChanged',colors);
  bridge.sync(lastUI);
  // Restore the native loader only after the document is ready to show.
  call(webApp,'ready');
  emit('vmeste:telegram-ready');
 }
 if(!requested)return;
 if(window.Telegram?.WebApp){initialize();return}
 // Load before app.js runs, without blocking the ordinary PWA or offline startup.
 const script=document.createElement('script');
 script.src='https://telegram.org/js/telegram-web-app.js?64';
 script.async=true;script.onload=initialize;
 document.head.appendChild(script);
})();

// Exercise the calendar interface entirely with synthetic data, including credentials.
import {spawn} from 'node:child_process';
import {mkdtemp, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import assert from 'node:assert/strict';
const base=process.env.APP_URL || 'http://127.0.0.1:18195';
const profile=await mkdtemp(join(tmpdir(),'minutes-calendar-browser-'));
const port=19289;
const chrome=spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',['--headless=new','--disable-background-networking','--disable-extensions','--no-first-run',`--user-data-dir=${profile}`,`--remote-debugging-port=${port}`,'--window-size=1440,1000',base],{stdio:'ignore'});
const delay=ms=>new Promise(r=>setTimeout(r,ms));let ws,seq=0;const pending=new Map();
try{
 let tabs;for(let i=0;i<60;i++){try{tabs=await(await fetch(`http://127.0.0.1:${port}/json`)).json();if(tabs.some(t=>t.type==='page'))break;}catch{}await delay(100);}
 ws=new WebSocket(tabs.find(t=>t.type==='page').webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j;});
 ws.onmessage=e=>{const data=JSON.parse(e.data);if(pending.has(data.id)){const p=pending.get(data.id);clearTimeout(p.timer);pending.delete(data.id);data.error?p.reject(data.error):p.resolve(data.result);}};
 function call(method,params={}){return new Promise((resolve,reject)=>{const id=++seq;const timer=setTimeout(()=>reject(new Error('Browser check timed out')),10000);pending.set(id,{resolve,reject,timer});ws.send(JSON.stringify({id,method,params}));});}
 async function evaluate(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});assert(!r.exceptionDetails,JSON.stringify(r.exceptionDetails));return r.result.value;}
 async function waitFor(expression){for(let i=0;i<60;i++){if(await evaluate(expression))return;await delay(100);}throw new Error('Missing UI state: '+expression);}
 await call('Page.enable');
 await call('Page.addScriptToEvaluateOnNewDocument',{source:`
  window.__calendarCalls=[];window.__syntheticSub=null;window.__skip=false;
  window.fetch=async(url,options={})=>{
    const reply=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});
    const body=options.body?JSON.parse(options.body):{};
    if(url.startsWith('/api/calendars'))window.__calendarCalls.push({url,method:options.method||'GET',body});
    if(url==='/api/calendars/discover')return reply([{href:'https://caldav.yandex.ru/test/',title:'Рабочий календарь'}]);
    if(url==='/api/calendars'&&options.method==='POST'){window.__syntheticSub={id:'sub',title:'Рабочий календарь',username:'test@yandex.ru',timezone:'Europe/Moscow',enabled:false,recording_authorized:false,analysis_types:['tldr'],lead_seconds:60,late_seconds:300,default_duration:60,last_sync:'2026-10-12T09:00:00Z'};return reply(window.__syntheticSub,201);}
    if(url==='/api/calendars/sub'&&options.method==='PATCH'){Object.assign(window.__syntheticSub,body);return reply(window.__syntheticSub);}
    if(url==='/api/calendars/occurrences/event/skip'){window.__skip=body.skipped;return reply({});}
    if(url==='/api/calendars/sub'&&options.method==='DELETE'){window.__syntheticSub=null;return reply({disconnected:true});}
    if(url==='/api/calendars')return reply({scheduler:{available:true},subscriptions:window.__syntheticSub?[window.__syntheticSub]:[],occurrences:window.__syntheticSub?[{id:'event',subscription_id:'sub',title:'Планёрка <img src=x onerror=alert(1)>',url:'https://telemost.yandex.ru/j/1234567890',starts_at:'2026-10-12T10:00:00Z',status:window.__skip?'blocked':(window.__syntheticSub.enabled?'scheduled':'paused'),reason:window.__skip?'user_skip':null,skipped:window.__skip,version:1}]:[],reasons:{user_skip:'Пропущено владельцем'}});
    if(url==='/api/analysis-types')return reply([{id:'tldr',name:'Коротко',default:true}]);
    return reply([]);
  };
 `});
 await call('Page.reload');
 await waitFor("document.getElementById('calendar-connect') && document.querySelectorAll('#upload-types input').length===1");
 await evaluate("document.getElementById('calendar-box').open=true");
 await waitFor("document.getElementById('calendar-subscriptions').textContent.includes('пока нет')");
 await evaluate("const f=document.getElementById('calendar-connect');f.elements.username.value='test@yandex.ru';f.elements.password.value='synthetic-password';document.getElementById('calendar-discover').click()");
 await waitFor("!document.getElementById('calendar-choice').hidden");
 await evaluate("document.getElementById('calendar-connect').requestSubmit()");
 await waitFor("document.getElementById('calendar-subscriptions').textContent.includes('Автоматический вход выключен')");
 assert(await evaluate("document.getElementById('calendar-connect').elements.password.value==='' && !document.querySelector('#calendar-occurrences img')"));
 await evaluate("document.querySelector('#calendar-subscriptions details').open=true;document.querySelector('#calendar-subscriptions form input[type=checkbox]').checked=true;document.querySelector('#calendar-subscriptions form').requestSubmit()");
 await waitFor("document.getElementById('calendar-occurrences').textContent.includes('Запланирован вход')");
 assert.equal(await evaluate("window.__calendarCalls.find(c=>c.method==='PATCH').body.recording_authorized"),true);
 await evaluate("[...document.querySelectorAll('#calendar-occurrences button')].find(b=>b.textContent==='Пропустить эту встречу').click()");
 await waitFor("document.getElementById('calendar-occurrences').textContent.includes('Пропущено владельцем')");
 await evaluate("document.getElementById('calendar-box').scrollIntoView()");
 const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile('/tmp/psptm-calendar-preview.png',Buffer.from(shot.data,'base64'));
 await evaluate("[...document.querySelectorAll('#calendar-subscriptions button')].find(b=>b.textContent==='Приостановить автоматический вход').click()");
 await waitFor("document.getElementById('calendar-subscriptions').textContent.includes('Автоматический вход выключен')");
 await evaluate("[...document.querySelectorAll('#calendar-subscriptions button')].find(b=>b.textContent==='Отключить календарь').click()");
 await waitFor("document.getElementById('calendar-subscriptions').textContent.includes('пока нет')");
 assert(await evaluate("!Object.values(localStorage).some(v=>v.includes('synthetic-password')) && !Object.values(sessionStorage).some(v=>v.includes('synthetic-password'))"));
 console.log('Calendar browser checks passed: connect, consent, schedule, skip, pause, disconnect, password cleared, safe text');
}finally{ws?.close();chrome.kill();for(const item of pending.values())clearTimeout(item.timer);}

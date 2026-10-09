// Local UI smoke check. Uses synthetic browser data; never submits a server job.
import {spawn} from 'node:child_process';
import {writeFile, mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import assert from 'node:assert/strict';

const chromePath=process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const base=process.env.APP_URL || 'http://127.0.0.1:18190';
const port=19287;
const profile=await mkdtemp(join(tmpdir(),'minutes-browser-'));
const chrome=spawn(chromePath,['--headless=new','--disable-background-networking','--disable-extensions','--disable-sync','--no-first-run',`--user-data-dir=${profile}`,`--remote-debugging-port=${port}`,'--window-size=1440,1000',base],{stdio:'ignore'});
let ws;
const delay=ms=>new Promise(r=>setTimeout(r,ms));
const pending=new Map();let id=0;
try{
  let tabs;
  for(let i=0;i<60;i++){try{tabs=await (await fetch(`http://127.0.0.1:${port}/json`)).json();if(tabs.some(t=>t.type==='page'))break;}catch{}await delay(200);}
  assert(tabs?.some(t=>t.type==='page'),'Chrome did not start');
  ws=new WebSocket(tabs.find(t=>t.type==='page').webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=()=>reject(new Error('Chrome connection failed'));});
  ws.onmessage=event=>{const data=JSON.parse(event.data);if(data.id&&pending.has(data.id)){const {resolve,reject,timer}=pending.get(data.id);clearTimeout(timer);pending.delete(data.id);if(data.error)reject(new Error('Browser command failed'));else resolve(data.result);}};
  function call(method,params={}){return new Promise((resolve,reject)=>{const seq=++id;const timer=setTimeout(()=>{pending.delete(seq);reject(new Error('Browser command timed out'));},10000);pending.set(seq,{resolve,reject,timer});ws.send(JSON.stringify({id:seq,method,params}));});}
  async function evaluate(expression){const result=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});assert(!result.exceptionDetails,'Browser script failed');return result.result.value;}
  async function waitFor(expression){for(let i=0;i<50;i++){if(await evaluate(expression))return;await delay(100);}throw new Error('UI did not reach expected state');}
  // Read-only live API checks without Authorization; never print private responses.
  for(const route of ['meetings','actions','outbox']){
    const response=await fetch(base+'/api/'+route);
    assert.equal(response.status,200,'API should work without a key');
    await response.body.cancel();
  }
  await call('Page.enable');
  // Reload with synthetic data so screenshots cannot contain private meeting titles.
  await call('Page.addScriptToEvaluateOnNewDocument',{source:`sessionStorage.setItem('minutes-key','obsolete-smoke-fixture');window.__uploadSmoke=null;window.__xhrCount=0;window.__nativeXHR=window.XMLHttpRequest;window.XMLHttpRequest=class{constructor(){this.upload={};this.headers={};}open(method,url){this.method=method;this.url=url;}setRequestHeader(name,value){this.headers[name]=value;}send(body){window.__xhr=this;window.__xhrCount++;window.__uploadSmoke={filename:body.get('recording').name,title:body.get('title'),authorization:!!this.headers.Authorization,tokenInBody:body.has('token')};}};const realFetch=window.fetch.bind(window);window.fetch=async(url,options={})=>{if(url==='/api/telemost/sessions'){if(options.method==='POST'){window.__telemostSmoke=JSON.parse(options.body);window.__telemostState={id:'00000000-0000-0000-0000-000000000003',title:'Синтетический тест Телемоста',status:'recording'};return new Response(JSON.stringify(window.__telemostState),{status:202});}return new Response(JSON.stringify(window.__telemostState?[window.__telemostState]:[]));}if(url==='/api/telemost/sessions/00000000-0000-0000-0000-000000000003/stop'){window.__telemostState.status='uploaded';window.__telemostState.meeting_id='browser-smoke-meeting';return new Response(JSON.stringify(window.__telemostState),{status:202});}if(url==='/api/meetings'&&options.method==='POST'){window.__uploadSmoke={filename:options.body.get('recording').name,title:options.body.get('title'),authorization:!!options.headers?.Authorization,tokenInBody:options.body.has('token')};return new Response(JSON.stringify({id:'browser-smoke-meeting'}),{status:201});}if(['/api/meetings','/api/actions','/api/outbox'].includes(url))return new Response('[]');if(url==='/api/meetings/browser-smoke-meeting')return new Response(JSON.stringify({id:'browser-smoke-meeting',title:'Проверка формы',status:'queued',transcript:[{start:1,end:2,text:'Синтетический исходник'}],actions:[],analysis:{status:'ready',step:'done',completed:1,total:1,error_code:null,rejected_count:0,reports:[{id:'tldr',name:'Коротко о встрече',status:'ready',error_code:null,content:'Синтетический отчёт <img src=x onerror=alert(1)> [00:00:01]'}]}}));return realFetch(url,options);};`});
  await call('Page.reload');
  await waitFor("document.getElementById('meeting-list')?.textContent.includes('Загруженных встреч пока нет')");
  await waitFor("document.querySelectorAll('#upload-types input').length===16");
  assert.equal(await evaluate("document.querySelectorAll('#upload-types input:checked').length"),5);
  assert(await evaluate("!document.getElementById('workspace').hidden && !document.getElementById('token') && !document.getElementById('login-form') && !document.getElementById('logout') && sessionStorage.getItem('minutes-key')===null"));
  assert(await evaluate("document.getElementById('recording').getBoundingClientRect().height > 0 && !document.getElementById('upload-button').disabled"));
  const image=await call('Page.captureScreenshot',{format:'png'});
  await writeFile(process.env.SCREENSHOT_PATH || 'docs/previews/workspace.png',Buffer.from(image.data,'base64'));
  await evaluate("const files=new DataTransfer();files.items.add(new File(['synthetic UI fixture'],'Проверка формы.wav',{type:'audio/wav'}));document.getElementById('recording').files=files.files;document.getElementById('recording').dispatchEvent(new Event('change'));");
  assert.equal(await evaluate("document.getElementById('upload').elements.title.value"),'Проверка формы');
  await evaluate("document.getElementById('upload').requestSubmit();");
  assert(await evaluate("!document.getElementById('upload-progress').hidden && document.getElementById('upload-button').disabled && document.getElementById('recording').disabled"));
  await evaluate("window.__xhr.upload.onprogress({loaded:512,total:1024,lengthComputable:true});");
  assert(await evaluate("document.getElementById('upload-meter').value===50 && document.getElementById('upload-status').textContent.includes('50%') && document.getElementById('upload-bytes').textContent.includes('из')"));
  await evaluate("document.getElementById('upload').requestSubmit();");
  assert.equal(await evaluate("window.__xhrCount"),1);
  const progressImage=await call('Page.captureScreenshot',{format:'png'});
  await writeFile((process.env.SCREENSHOT_PATH || 'docs/previews/workspace.png').replace('.png','-progress.png'),Buffer.from(progressImage.data,'base64'));
  await evaluate("window.__xhr.onerror();");
  await waitFor("!document.getElementById('upload-button').disabled && document.getElementById('notice').textContent.includes('Связь прервалась')");
  assert(await evaluate("document.getElementById('recording').files.length===1 && document.getElementById('upload-status').textContent.includes('не подтверждена')"));
  await evaluate("document.getElementById('upload').requestSubmit();window.__xhr.upload.onprogress({loaded:1024,total:1024,lengthComputable:true});window.__xhr.upload.onload();");
  assert(await evaluate("document.getElementById('upload-meter').value===100 && document.getElementById('upload-status').textContent.includes('сохраняет') && document.getElementById('upload-button').disabled"));
  await evaluate("window.__xhr.status=201;window.__xhr.response={id:'browser-smoke-meeting'};window.__xhr.onload();");
  await waitFor("document.getElementById('notice').textContent==='Запись сохранена локально'");
  assert(await evaluate("document.getElementById('upload-status').textContent.includes('передана на обработку')"));
  await waitFor("!!document.querySelector('#analysis-panel .report-content')");
  assert(await evaluate("document.querySelector('#analysis-panel .report-content').textContent.includes('Синтетический отчёт') && !document.querySelector('#analysis-panel img')"));
  await evaluate("document.querySelector('#analysis-panel .time-link').click()");
  assert(await evaluate("document.querySelector('.segment').closest('details').open"));
  assert.deepEqual(await evaluate("window.__uploadSmoke"),{filename:'Проверка формы.wav',title:'Проверка формы',authorization:false,tokenInBody:false});
  assert(await evaluate("!document.getElementById('upload-button').disabled && document.getElementById('recording').files.length===0 && document.querySelectorAll('#upload-types input:checked').length===5"));
  await evaluate("document.getElementById('registry-tab').click()");
  await waitFor("!document.getElementById('registry-view').hidden && document.getElementById('registry').textContent.includes('Реестр пока пуст')");
  await evaluate("document.getElementById('meetings-tab').click()");
  await waitFor("!document.getElementById('meetings-view').hidden");
  await evaluate("document.getElementById('telemost-box').open=true");
  await waitFor("document.getElementById('telemost-sessions').textContent.includes('готов к тестовому')");
  await evaluate("const f=document.getElementById('telemost-form');f.elements.url.value='https://telemost.yandex.ru/j/1234567890';f.elements.title.value='Синтетический тест Телемоста';f.elements.duration_minutes.value='2';f.elements.recording_authorized.checked=true;f.requestSubmit()");
  await waitFor("document.getElementById('telemost-sessions').textContent.includes('Записываем встречу')");
  assert.deepEqual(await evaluate("window.__telemostSmoke"),{url:'https://telemost.yandex.ru/j/1234567890',title:'Синтетический тест Телемоста',duration_minutes:2,recording_authorized:true});
  await evaluate("document.querySelector('#telemost-sessions button').click()");
  await waitFor("document.getElementById('telemost-sessions').textContent.includes('Передано на обработку') && document.getElementById('telemost-sessions').textContent.includes('Открыть встречу')");
  // Exercise the native XHR against the actual API with an empty file (422, no AI job).
  await evaluate("window.XMLHttpRequest=window.__nativeXHR;const emptyFiles=new DataTransfer();emptyFiles.items.add(new File([],'empty.wav',{type:'audio/wav'}));document.getElementById('recording').files=emptyFiles.files;document.getElementById('recording').dispatchEvent(new Event('change'));document.getElementById('upload').requestSubmit();");
  await waitFor("document.getElementById('notice').textContent==='Пустая запись' && !document.getElementById('upload-button').disabled");
  assert(await evaluate("document.getElementById('recording').files.length===1 && document.getElementById('upload-status').textContent.includes('не подтверждена')"));
  console.log('Upload progress, bytes, duplicate-submit lock, transfer/saving distinction, network-error retry and completion OK. Live API reads and native XHR empty-file rejection OK; no server job created.');
  await call('Browser.close');
}finally{
  ws?.close();
  chrome.kill('SIGTERM');
}

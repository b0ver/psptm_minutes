const $ = (id) => document.getElementById(id);
// Remove credentials left by the previous UI version.
sessionStorage.removeItem('minutes-key');
let selected = null;
let busy = false;
let analysisTypes=[];let analysisActive=false;const analysisChoices={},reportChoices={};
const labels = {queued:'В очереди', processing:'Обрабатывается', ready:'Готово к проверке', failed:'Ошибка', draft:'Черновик', approved:'Утверждено', in_progress:'В работе', done:'Выполнено', pending:'Ожидает отправки', sent:'Доставлено'};
function node(tag, text, cls) { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n; }
function notify(text) { $('notice').textContent=text; $('notice').hidden=false; setTimeout(()=>$('notice').hidden=true,7000); }
async function api(path, options={}) {
  const headers={...(options.headers||{})};
  const r=await fetch('/api/'+path,{...options,headers});
  let data=await r.json();
  if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'Проверьте поля формы');
  return data;
}
const json = (method, body) => ({method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
function badge(status) { return node('span',labels[status]||status,'badge '+status); }
function uploadAccess(){ $('upload-button').disabled=busy;$('recording').disabled=busy;$('upload').querySelectorAll('input').forEach(input=>input.disabled=busy);$('upload').setAttribute('aria-busy',String(busy));if(!busy)$('upload-button').textContent='Загрузить и обработать'; }
function sizeLabel(bytes){const units=['Б','КиБ','МиБ','ГиБ','ТиБ'];let unit=0;while(bytes>=1024&&unit<units.length-1){bytes/=1024;unit++;}return new Intl.NumberFormat('ru',{maximumFractionDigits:1}).format(bytes)+' '+units[unit];}
function uploadState(stage,loaded=0,total=0){
  $('upload-progress').hidden=false;
  const meter=$('upload-meter');
  if(stage==='uploading'){
    if(total){const percent=Math.min(100,Math.floor(loaded/total*100));meter.value=percent;$('upload-status').textContent='Загружаем запись: '+percent+'%';$('upload-bytes').textContent=sizeLabel(loaded)+' из '+sizeLabel(total);}
    else{meter.removeAttribute('value');$('upload-status').textContent='Начинаем загрузку записи…';$('upload-bytes').textContent=loaded?sizeLabel(loaded)+' передано':'';}
    $('upload-button').textContent='Загружается…';
  }else if(stage==='saving'){
    meter.value=100;$('upload-status').textContent='Файл передан. Сервер сохраняет запись…';$('upload-button').textContent='Сохраняем…';
  }else if(stage==='done'){
    meter.value=100;$('upload-status').textContent='Запись загружена и передана на обработку.';
  }else{
    $('upload-status').textContent='Загрузка не подтверждена. Проверьте список встреч перед повторной отправкой.';
  }
}
function sendRecording(body){
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();xhr.open('POST','/api/meetings');xhr.responseType='json';
    xhr.upload.onprogress=(e)=>uploadState('uploading',e.loaded,e.lengthComputable?e.total:0);
    xhr.upload.onload=()=>uploadState('saving');
    xhr.onload=()=>{if(xhr.status>=200&&xhr.status<300)resolve(xhr.response);else reject(new Error(typeof xhr.response?.detail==='string'?xhr.response.detail:'Сервер не принял запись'));};
    xhr.onerror=()=>reject(new Error('Связь прервалась. Проверьте список встреч перед повторной загрузкой'));
    xhr.onabort=()=>reject(new Error('Загрузка прервана. Проверьте список встреч перед повторной загрузкой'));
    xhr.send(body);
  });
}
async function refresh(){
  if(busy)return;
  const meetings=await api('meetings');
  $('meeting-list').replaceChildren();
  for(const m of meetings){const b=node('button',undefined,'meeting');b.append(node('strong',m.title),node('br'),badge(m.status));b.onclick=()=>openMeeting(m.id).catch(e=>notify(e.message));$('meeting-list').append(b);}
  if(!meetings.length)$('meeting-list').append(node('p','Загруженных встреч пока нет.','muted'));
}
function field(label, type, value){const l=node('label',label);const input=node(type==='textarea'?'textarea':'input');if(type!=='textarea')input.type=type;input.value=value||'';l.append(input);return [l,input];}
function taskCard(a, full=true){
  const box=node('div',undefined,'task');box.append(badge(a.status));
  if(a.status==='draft'){
    const [t,ti]=field('Поручение','textarea',a.text);ti.maxLength=1500;box.append(t);
    const row=node('div',undefined,'row');const [o,oi]=field('Ответственный','text',a.owner);oi.maxLength=200;const [d,di]=field('Срок','date',a.due_on);row.append(o,d);box.append(row);
    const [z,zi]=field('Часовой пояс','text',a.timezone);box.append(z);
    const buttons=node('div',undefined,'buttons');const save=node('button','Сохранить','secondary');const approve=node('button','Утвердить и отправить в Telegram');
    save.onclick=async()=>{save.disabled=true;try{await api('actions/'+a.id,json('PATCH',{version:a.version,text:ti.value,owner:oi.value||null,due_on:di.value||null,timezone:zi.value}));await reload();notify('Черновик сохранён');}catch(e){notify(e.message);}finally{save.disabled=false;}};
    approve.onclick=async()=>{if(ti.value!==a.text|| (oi.value||null)!==a.owner || (di.value||null)!==a.due_on || zi.value!==a.timezone){notify('Сначала сохраните изменения');return;}if(!confirm('Утвердить поручение и поставить его карточку в очередь Telegram?'))return;approve.disabled=true;try{await api('actions/'+a.id+'/approve',json('POST',{version:a.version}));await reload();notify('Утверждено. Доставка зависит от подключения Telegram.');}catch(e){notify(e.message);}finally{approve.disabled=false;}};
    buttons.append(save,approve);box.append(buttons);
  }else{
    box.append(node('p',a.text));box.append(node('p',`Ответственный: ${a.owner||'не указан'} · Срок: ${a.due_on||'не указан'} (${a.timezone})`,'muted'));
    if(['approved','in_progress'].includes(a.status)){const buttons=node('div',undefined,'buttons');for(const status of ['in_progress','done']){if(a.status===status)continue;const b=node('button',labels[status],'secondary');b.onclick=async()=>{b.disabled=true;try{await api('actions/'+a.id+'/status',json('POST',{version:a.version,status}));await reload();}catch(e){notify(e.message);}finally{b.disabled=false;}};buttons.append(b);}box.append(buttons);}
  }
  if(full)box.append(node('blockquote',`[${Math.floor(a.source_seconds/60)}:${String(a.source_seconds%60).padStart(2,'0')}] ${a.evidence}`));
  box.append(node('small','ID: '+a.id+' · версия '+a.version));return box;
}
async function openMeeting(id){
  selected=id;const m=await api('meetings/'+id);const detail=$('detail');detail.className='panel';detail.replaceChildren(node('h2',m.title),badge(m.status));
  if(m.recording_complete===false)detail.append(node('p','Неполная запись: выводы относятся только к сохранённой части встречи.','muted'));
  if(m.error_code){const message=m.error_code==='unverified_action_candidates'?'Некоторые предложенные поручения не прошли проверку цитат и не добавлены в реестр. Проверьте поручения по транскрипту.':(m.transcript?'Транскрипт сохранён. Не удалось составить протокол: ':'Не удалось обработать запись: ')+m.error_code;detail.append(node('p',message,'muted'));}
  if(m.minutes){detail.append(node('p','Черновик протокола. Проверьте по записи перед использованием.','muted'),node('div',m.minutes,'minutes'));}
  else if(m.status==='failed')detail.append(node('p',m.transcript?'Транскрипт доступен ниже.':'Распознавание не завершено.','muted'));else detail.append(node('p','Запись ждёт обработки или обрабатывается.','muted'));
  if(m.transcript){const details=node('details');details.append(node('summary','Транскрипт с временными метками'));for(const s of m.transcript){const p=node('p',undefined,'segment');p.dataset.start=s.start;p.append(node('strong',`${Math.floor(s.start/60)}:${String(Math.floor(s.start%60)).padStart(2,'0')}`),document.createTextNode((s.speaker_hint?` ${s.speaker_hint} (вероятный спикер): `:' ')+s.text));details.append(p);}detail.append(details);}
  const analyses=node('section');analyses.id='analysis-panel';detail.append(analyses);renderAnalysis(m.id,m.analysis);
  if(m.status==='ready'){detail.append(node('h2','Поручения'));if(!m.actions.length)detail.append(node('p','Поручений не найдено.','muted'));for(const a of m.actions)detail.append(taskCard(a));}
}
async function registry(){
  const actions=await api('actions');$('registry').replaceChildren();if(!actions.length)$('registry').append(node('p','Реестр пока пуст.','muted'));for(const a of actions)$('registry').append(taskCard(a));
  const delivery=await api('outbox');$('delivery').replaceChildren();if(!delivery.length)$('delivery').append(node('p','Утверждённые поручения появятся здесь.','muted'));for(const o of delivery){const row=node('p',o.action_id+' · ');row.append(badge(o.status),document.createTextNode(' · попыток: '+o.attempts));$('delivery').append(row);}
}
async function reload(){await refresh();if(!$('registry-view').hidden)await registry();else if(selected)await openMeeting(selected);}
$('meetings-tab').onclick=()=>{$('meetings-view').hidden=false;$('registry-view').hidden=true;$('meetings-tab').className='active';$('registry-tab').className='secondary';reload().catch(e=>notify(e.message));};
$('registry-tab').onclick=()=>{$('meetings-view').hidden=true;$('registry-view').hidden=false;$('registry-tab').className='active';$('meetings-tab').className='secondary';registry().catch(e=>notify(e.message));};
$('recording').onchange=()=>{$('upload-progress').hidden=true;const file=$('recording').files[0];const title=$('upload').elements.title;if(file&&!title.value.trim())title.value=file.name.replace(/\.[^.]+$/,'').slice(0,200);};
$('upload').onsubmit=async(e)=>{
  e.preventDefault();if(busy)return;
  const form=e.currentTarget;const file=form.elements.recording.files[0];
  if(!file){notify('Выберите запись');return;}
  const body=new FormData(form);const types=[...$('upload-types').querySelectorAll('input:checked')].map(input=>input.value);if(analysisTypes.length&&!types.length){notify('Выберите хотя бы один тип разбора');return;}if(types.length)body.set('analysis_types',JSON.stringify(types));busy=true;uploadAccess();uploadState('uploading');
  let saved=false;
  try{
    const result=await sendRecording(body);saved=true;form.reset();$('upload-types-summary').textContent='Типы разборов: '+$('upload-types').querySelectorAll('input:checked').length;selected=result.id;
    $('meetings-view').hidden=false;$('registry-view').hidden=true;$('meetings-tab').className='active';$('registry-tab').className='secondary';
    uploadState('done');busy=false;await reload();notify('Запись сохранена локально');
  }catch(err){if(!saved)uploadState('error');notify(saved?'Запись загружена. Не удалось обновить список встреч — обновите страницу.':err.message);}
  finally{busy=false;uploadAccess();}
};
setInterval(()=>{if(analysisActive&&selected&&!document.querySelector('#analysis-panel input:focus'))api('meetings/'+selected+'/analysis').then(state=>renderAnalysis(selected,state)).catch(()=>{});if(!busy&&!document.querySelector('.task input:focus,.task textarea:focus'))refresh().then(async()=>{if(selected&&!$('meetings-view').hidden){const m=await api('meetings/'+selected);if(['queued','processing'].includes(m.status)||!$('detail').querySelector('.task'))await openMeeting(selected);}}).catch(()=>{});},5000);
refresh().catch(e=>notify(e.message));
loadAnalysisTypes().catch(e=>notify(e.message));

async function loadAnalysisTypes(){
  analysisTypes=await api('analysis-types');
  const box=$('upload-types');box.replaceChildren();
  for(const t of analysisTypes){const label=node('label',undefined,'type-choice');const input=node('input');input.type='checkbox';input.value=t.id;input.defaultChecked=t.default;input.checked=t.default;input.onchange=()=>{$('upload-types-summary').textContent='Типы разборов: '+box.querySelectorAll('input:checked').length;};label.title=t.description;label.append(input,document.createTextNode(t.name));box.append(label);}
  $('upload-types-summary').textContent='Типы разборов: '+box.querySelectorAll('input:checked').length;
}
function jumpToTimestamp(value){
  const parts=value.split(':').map(Number);const seconds=parts[0]*3600+parts[1]*60+parts[2];
  const segments=[...$('detail').querySelectorAll('.segment')];
  const target=segments.find(s=>Number(s.dataset.start)>=seconds)||segments.at(-1);
  if(target){target.closest('details').open=true;target.scrollIntoView({behavior:'smooth',block:'center'});}
}
function inlineText(parent,text){
  const pattern=/(\*\*[^*]+\*\*|\[\d{2}:\d{2}:\d{2}\])/g;let last=0;
  for(const match of text.matchAll(pattern)){parent.append(document.createTextNode(text.slice(last,match.index)));const token=match[0];if(token.startsWith('**'))parent.append(node('strong',token.slice(2,-2)));else{const button=node('button',token,'time-link');button.type='button';button.onclick=()=>jumpToTimestamp(token.slice(1,-1));parent.append(button);}last=match.index+token.length;}
  parent.append(document.createTextNode(text.slice(last)));
}
function markdown(text){
  const box=node('div',undefined,'report-content');let table=null,list=null;
  for(const line of text.split('\n')){
    if(!line.trim()){table=null;list=null;continue;}
    if(/^\s*\|.*\|\s*$/.test(line)){
      const cells=line.trim().slice(1,-1).split(/(?<!\\)\|/).map(c=>c.trim().replace(/\\\|/g,'|'));
      if(cells.every(c=>/^:?-+:?$/.test(c)))continue;
      const header=!table;if(!table){const wrap=node('div',undefined,'report-table');table=node('table');wrap.append(table);box.append(wrap);}const row=node('tr');for(const cell of cells){const el=node(header?'th':'td');inlineText(el,cell);row.append(el);}table.append(row);list=null;continue;
    }
    table=null;const heading=line.match(/^(#{1,4})\s+(.*)/);
    if(heading){const el=node('h'+Math.min(heading[1].length+1,5));inlineText(el,heading[2]);box.append(el);list=null;continue;}
    if(/^[-*]\s+/.test(line)){if(!list){list=node('ul');box.append(list);}const item=node('li');inlineText(item,line.replace(/^[-*]\s+/,''));list.append(item);continue;}
    list=null;const p=node('p');inlineText(p,line);box.append(p);
  }
  return box;
}
function renderAnalysis(meetingId,state){
  if(selected!==meetingId||!$('analysis-panel'))return;
  const panel=$('analysis-panel');panel.replaceChildren(node('h2','Разборы встречи'));
  const labels={extract:'Извлекаем факты',merge:'Собираем итоги',verify:'Проверяем цитаты',render:'Готовим отчёты',waiting_transcript:'Ожидаем транскрипт',done:'Разборы готовы'};
  analysisActive=['queued','processing'].includes(state.status);
  if(analysisActive){const p=node('p',state.status==='queued'?'Разборы в очереди':(labels[state.step]||'Обрабатываем')+' · '+state.completed+' / '+state.total,'muted');panel.append(p);const meter=node('progress');meter.max=Math.max(1,state.total);if(state.total)meter.value=state.completed;panel.append(meter);}
  if(state.error_code)panel.append(node('p','Не удалось завершить разборы: '+state.error_code+'. Можно повторить выбранные типы.','muted'));
  if(state.rejected_count)panel.append(node('p','Не подтверждены цитатами и исключены из фактов: '+state.rejected_count+'. Проверьте исходный транскрипт.','muted'));
  if(!analysisChoices[meetingId])analysisChoices[meetingId]=state.reports.length?state.reports.map(r=>r.id):analysisTypes.filter(t=>t.default).map(t=>t.id);
  const picker=node('details',undefined,'analysis-selector');picker.append(node('summary','Добавить типы разборов'));const options=node('div',undefined,'type-options');
  for(const t of analysisTypes){const label=node('label',undefined,'type-choice');const input=node('input');input.type='checkbox';input.value=t.id;input.checked=analysisChoices[meetingId].includes(t.id);input.onchange=()=>{analysisChoices[meetingId]=[...options.querySelectorAll('input:checked')].map(i=>i.value);};label.title=t.description;label.append(input,document.createTextNode(t.name));options.append(label);}picker.append(options);
  const request=node('button','Подготовить выбранные разборы');request.type='button';request.onclick=async()=>{request.disabled=true;try{const next=await api('meetings/'+meetingId+'/analysis',json('POST',{types:analysisChoices[meetingId]}));renderAnalysis(meetingId,next);notify('Типы добавлены. Готовые отчёты используются из кэша.');}catch(e){notify(e.message);request.disabled=false;}};picker.append(request);panel.append(picker);
  const nav=node('div',undefined,'report-tabs');const reports=state.reports;let active=reportChoices[meetingId];if(!reports.some(r=>r.id===active))active=reports.find(r=>r.status==='ready')?.id||reports[0]?.id;reportChoices[meetingId]=active;
  for(const r of reports){const b=node('button',r.name+(r.status==='ready'?'':' · '+(labels[r.status]||({'queued':'в очереди','processing':'готовится','failed':'ошибка'})[r.status])),r.id===active?'':'secondary');b.type='button';b.onclick=()=>{reportChoices[meetingId]=r.id;renderAnalysis(meetingId,state);};nav.append(b);}panel.append(nav);
  const report=reports.find(r=>r.id===active);if(report?.content){panel.append(node('p','AI-черновик. Проверьте по транскрипту; разбор не меняет утверждённые поручения.','muted'),markdown(report.content));const download=node('button','Скачать Markdown','secondary');download.type='button';download.onclick=()=>{const url=URL.createObjectURL(new Blob([report.content],{type:'text/markdown;charset=utf-8'}));const a=node('a');a.href=url;a.download=report.id+'.md';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};panel.append(download);}
  else if(report)panel.append(node('p',report.status==='failed'?'Этот разбор не завершён. Повторите его через выбор типов.':'Отчёт появится после обработки.','muted'));
}

const telemostLabels={opening:'Открываем страницу встречи',preparing:'Готовим гостевой вход',starting:'Открываем Телемост',waiting_host:'Подключаемся к звонку / ожидаем допуска',joined:'Ассистент подключён',recording:'Записываем встречу',stopping:'Завершаем запись',finishing:'Сохраняем файл',saved:'Запись сохранена',uploading:'Передаём на транскрибацию',uploaded:'Передано на обработку',failed:'Ошибка',cancelled:'Отменено',upload_uncertain:'Запись сохранена, передачу нужно повторить',partial_saved:'Сохранена часть записи'};
const telemostErrors={telemost_ui_timeout:'Не удалось пройти экран входа или дождаться допуска. Откройте снимок состояния.',local_recording_unavailable:'В этой встрече локальная запись недоступна.',recording_download_timeout:'Телемост не передал файл записи. Состояние сохранено.',upload_result_unknown:'Запись сохранена на сервере. Повторная передача проверит, не была ли она уже принята.',agent_interrupted:'Сессия прервана при перезапуске агента.',recording_import_rejected:'Приложение не приняло запись. Файл сохранён на сервере.'};
let telemostSubmitting=false;
async function refreshTelemost(){
  const target=$('telemost-sessions');
  try{
    const sessions=await api('telemost/sessions');target.replaceChildren();
    for(const s of sessions){const card=node('div',undefined,'task');card.append(node('strong',s.title),node('p',telemostLabels[s.status]||s.status));if(s.recording_started_at&&['recording','stopping','finishing'].includes(s.status))card.append(node('p','Длительность: '+Math.floor((Date.now()/1000-s.recording_started_at)/60)+' мин.','muted'));if(s.error_code)card.append(node('p',telemostErrors[s.error_code]||'Обработка остановлена: '+s.error_code,'muted'));
      const controls=node('div',undefined,'buttons');if(!['uploaded','failed','cancelled','upload_uncertain','partial_saved','uploading','saved'].includes(s.status)){const stop=node('button','Завершить и обработать','secondary');stop.type='button';stop.onclick=async()=>{stop.disabled=true;try{await api('telemost/sessions/'+s.id+'/stop',json('POST',{}));await refreshTelemost();}catch(e){notify(e.message);stop.disabled=false;}};controls.append(stop);}
      if(s.chunks)card.append(node('p','На диске: '+s.chunks+' частей аудио · '+Math.round(s.checkpoint_bytes/1024)+' КиБ','muted'));
      if(s.recording_complete===false)card.append(node('p','Неполная запись: часть встречи отсутствует.','muted'));
      if(['delayed','failed','unavailable'].includes(s.capture_health))card.append(node('p','Аварийное сохранение: '+({delayed:'части задерживаются',failed:'ошибка сохранения',unavailable:'недоступно, остаётся штатная запись'}[s.capture_health]),'muted'));
      if(['partial_saved','upload_uncertain'].includes(s.status)){const retry=node('button',s.status==='partial_saved'?'Обработать сохранённую часть':'Повторить передачу','secondary');retry.type='button';retry.onclick=async()=>{retry.disabled=true;try{await api('telemost/sessions/'+s.id+'/import',json('POST',{}));await refreshTelemost();}catch(e){notify(e.message);retry.disabled=false;}};controls.append(retry);}
      if(s.meeting_id){const open=node('button','Открыть встречу');open.type='button';open.onclick=()=>{$('meetings-tab').click();openMeeting(s.meeting_id).catch(e=>notify(e.message));};controls.append(open);}
      if(s.has_preview){const preview=node('a','Снимок состояния');preview.href='/api/telemost/sessions/'+s.id+'/preview';preview.target='_blank';preview.rel='noopener';controls.append(preview);}card.append(controls);target.append(card);
    }
    if(!sessions.length)target.append(node('p','Ассистент готов к тестовому подключению.','muted'));
  }catch(e){target.replaceChildren(node('p',e.message,'muted'));}
}
$('telemost-form').onsubmit=async(e)=>{e.preventDefault();if(telemostSubmitting)return;const form=e.currentTarget;const body={url:form.elements.url.value.trim(),title:form.elements.title.value.trim(),duration_minutes:Number(form.elements.duration_minutes.value),recording_authorized:form.elements.recording_authorized.checked};telemostSubmitting=true;$('telemost-start').disabled=true;try{await api('telemost/sessions',json('POST',body));await refreshTelemost();notify('Ассистент подключается к встрече');}catch(err){notify(err.message);}finally{telemostSubmitting=false;$('telemost-start').disabled=false;}};
$('telemost-box').addEventListener('toggle',()=>{if($('telemost-box').open)refreshTelemost();});
setInterval(()=>{if($('telemost-box').open)refreshTelemost();},5000);

let calendarBusy=false;
const calendarStatuses={scheduled:'Запланирован вход',paused:'Автоподключение выключено',blocked:'Пропуск события',cancelled:'Отменено',missed:'Встреча пропущена',dispatching:'Подключаемся',uncertain:'Проверяем результат входа',started:'Ассистент подключается или записывает',finished:'Сессия завершена',failed:'Ошибка запуска'};
function calendarDate(value,zone){return new Intl.DateTimeFormat('ru',{dateStyle:'short',timeStyle:'short',timeZone:zone}).format(new Date(value));}
async function calendarOperation(button,operation){button.disabled=true;try{await operation();await refreshCalendars(true);}catch(e){notify(e.message);}finally{button.disabled=false;}}
async function refreshCalendars(force=false){
  if(!force && $('calendar-subscriptions').contains(document.activeElement))return;
  const data=await api('calendars');const subscriptions=$('calendar-subscriptions');subscriptions.replaceChildren();
  $('calendar-scheduler-status').textContent=data.scheduler?.available?'Планировщик работает':'Планировщик пока недоступен. Автоматический вход сейчас не выполняется.';
  for(const sub of data.subscriptions){
    const card=node('div',undefined,'task');card.append(node('strong',sub.title),node('p',sub.username+' · '+sub.timezone,'muted'),node('p',sub.enabled?'Автоматический вход и запись включены':'Автоматический вход выключен'));
    card.append(node('p',sub.last_sync?'Последнее обновление: '+calendarDate(sub.last_sync,sub.timezone):'Календарь ещё не обновлён','muted'));
    if(sub.error_code)card.append(node('p',data.reasons[sub.error_code]||'Ошибка синхронизации календаря','muted'));
    const settings=node('details');settings.append(node('summary','Настройки автоматического входа'));
    const form=node('form');const authorize=node('input');authorize.type='checkbox';authorize.checked=sub.recording_authorized;const authorization=node('label',undefined,'type-choice');authorization.append(authorize,document.createTextNode('Разрешаю автоматический вход и запись встреч выбранного календаря; разрешение организаторов и участников получено'));form.append(authorization);
    const choices=node('div',undefined,'type-options');for(const type of analysisTypes){const label=node('label',undefined,'type-choice');const input=node('input');input.type='checkbox';input.value=type.id;input.checked=sub.analysis_types.includes(type.id);label.append(input,document.createTextNode(type.name));choices.append(label);}form.append(node('p','Типы разборов'),choices);
    const [leadLabel,lead]=field('Входить заранее, секунд','number',sub.lead_seconds);lead.value=sub.lead_seconds;lead.min=0;lead.max=300;lead.required=true;
    const [lateLabel,late]=field('Допустимое опоздание, секунд','number',sub.late_seconds);late.value=sub.late_seconds;late.min=0;late.max=300;late.required=true;
    const [durationLabel,duration]=field('Предел записи без времени окончания, минут','number',sub.default_duration);duration.min=1;duration.max=120;duration.required=true;form.append(leadLabel,lateLabel,durationLabel);
    const save=node('button',sub.enabled?'Сохранить настройки':'Включить автоматический вход');save.type='submit';form.append(save);form.onsubmit=e=>{e.preventDefault();if(!authorize.checked){notify('Подтвердите разрешение на автоматическую запись');return;}calendarOperation(save,async()=>{await api('calendars/'+sub.id,json('PATCH',{enabled:true,recording_authorized:true,analysis_types:[...choices.querySelectorAll('input:checked')].map(i=>i.value),lead_seconds:Number(lead.value),late_seconds:Number(late.value),default_duration:Number(duration.value)}));notify('Автоматический вход включён');});};settings.append(form);card.append(settings);
    const controls=node('div',undefined,'buttons');const sync=node('button','Обновить сейчас','secondary');sync.type='button';sync.onclick=()=>calendarOperation(sync,()=>api('calendars/'+sub.id+'/sync',json('POST',{})));controls.append(sync);
    if(sub.enabled){const pause=node('button','Приостановить автоматический вход','secondary');pause.type='button';pause.onclick=()=>calendarOperation(pause,()=>api('calendars/'+sub.id,json('PATCH',{enabled:false,recording_authorized:sub.recording_authorized,analysis_types:sub.analysis_types,lead_seconds:sub.lead_seconds,late_seconds:sub.late_seconds,default_duration:sub.default_duration})));controls.append(pause);}
    const disconnect=node('button','Отключить календарь','secondary');disconnect.type='button';disconnect.onclick=()=>calendarOperation(disconnect,()=>api('calendars/'+sub.id,{method:'DELETE'}));controls.append(disconnect);card.append(controls);subscriptions.append(card);
  }
  if(!data.subscriptions.length)subscriptions.append(node('p','Подключённых календарей пока нет.','muted'));
  const events=$('calendar-occurrences');events.replaceChildren();const byId=new Map(data.subscriptions.map(s=>[s.id,s]));
  for(const event of data.occurrences){const sub=byId.get(event.subscription_id);const card=node('div',undefined,'task');card.append(node('strong',event.title),node('p',calendarDate(event.starts_at,sub.timezone)+' · '+sub.timezone+' · '+sub.title,'muted'),node('p',calendarStatuses[event.status]||event.status));if(event.reason)card.append(node('p',data.reasons[event.reason]||'Не удалось запустить встречу','muted'));if(event.url){const link=node('a','Ссылка Телемоста');link.href=event.url;link.target='_blank';link.rel='noopener';card.append(link);}const controls=node('div',undefined,'buttons');
    if(['scheduled','paused','blocked','cancelled','dispatching'].includes(event.status)){const skip=node('button',event.skipped?'Вернуть в расписание':'Пропустить эту встречу','secondary');skip.type='button';skip.onclick=()=>calendarOperation(skip,()=>api('calendars/occurrences/'+event.id+'/skip',json('POST',{version:event.version,skipped:!event.skipped})));controls.append(skip);}
    if(event.may_overlap)card.append(node('p','Возможное пересечение: агент обслуживает одну встречу. Текущая запись имеет приоритет.','muted'));
    const snapshot=event.session_snapshot;if(snapshot){card.append(node('p',telemostLabels[snapshot.status]||snapshot.status));if(snapshot.error_code)card.append(node('p',telemostErrors[snapshot.error_code]||'Сессия завершилась с ошибкой','muted'));if(snapshot.recording_complete===false)card.append(node('p','Неполная запись: часть встречи отсутствует.','muted'));}
    if(event.session_id){const manage=node('button','Управление записью','secondary');manage.type='button';manage.onclick=()=>{$('telemost-box').open=true;$('telemost-box').scrollIntoView({behavior:'smooth'});refreshTelemost();};controls.append(manage);}
    if(snapshot?.meeting_id){const open=node('button','Открыть протокол');open.type='button';open.onclick=()=>{$('meetings-tab').click();openMeeting(snapshot.meeting_id).catch(e=>notify(e.message));};controls.append(open);}card.append(controls);events.append(card);
  }
  if(!data.occurrences.length)events.append(node('p','После подключения здесь появятся ближайшие события за 14 дней.','muted'));
}
$('calendar-discover').onclick=async()=>{if(calendarBusy)return;const form=$('calendar-connect');if(!form.reportValidity())return;calendarBusy=true;$('calendar-discover').disabled=true;$('calendar-connect-status').textContent='Ищем календари…';try{const calendars=await api('calendars/discover',json('POST',{username:form.elements.username.value.trim(),password:form.elements.password.value}));$('calendar-select').replaceChildren();for(const calendar of calendars){const option=node('option',calendar.title);option.value=calendar.href;$('calendar-select').append(option);}$('calendar-choice').hidden=false;$('calendar-connect-status').textContent='Выберите календарь. Автоматический вход включается отдельно.';}catch(e){$('calendar-connect-status').textContent=e.message;}finally{calendarBusy=false;$('calendar-discover').disabled=false;}};
$('calendar-connect').addEventListener('input',e=>{if(['username','password'].includes(e.target.name)){$('calendar-choice').hidden=true;$('calendar-select').replaceChildren();}});
$('calendar-connect').onsubmit=async(e)=>{e.preventDefault();if(calendarBusy||!$('calendar-select').value)return;const form=e.currentTarget;calendarBusy=true;$('calendar-save').disabled=true;$('calendar-connect-status').textContent='Подключаем и читаем события…';try{await api('calendars',json('POST',{username:form.elements.username.value.trim(),password:form.elements.password.value,calendar_href:form.elements.calendar_href.value,timezone:form.elements.timezone.value.trim()}));form.elements.password.value='';$('calendar-choice').hidden=true;$('calendar-select').replaceChildren();$('calendar-connect-status').textContent='Календарь подключён. Проверьте расписание и включите автоматический вход в настройках подписки.';await refreshCalendars(true);}catch(err){$('calendar-connect-status').textContent=err.message;}finally{calendarBusy=false;$('calendar-save').disabled=false;}};
$('calendar-box').addEventListener('toggle',()=>{if($('calendar-box').open)refreshCalendars().catch(e=>notify(e.message));});
setInterval(()=>{if($('calendar-box').open)refreshCalendars().catch(e=>notify(e.message));},10000);

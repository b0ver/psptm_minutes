"""Offline Chromium check of real agent orchestration and continuous audio.

Synthetic page only. This does not prove Telemost's actual media-source lifetime.
"""
import asyncio
import json
import math
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import av
import httpx
from playwright.async_api import async_playwright

import service


HTML = '''<!doctype html><meta charset="utf-8"><input type="text">
<button id="join">Подключиться</button>
<button id="leave" hidden aria-label="Выйти из встречи">Выйти</button>
<button aria-label="Участники">Участники 2</button>
<button id="more" aria-label="Ещё">Ещё</button>
<div id="menu" hidden><button id="begin">Записать на компьютер</button></div>
<button id="end" hidden aria-label="Остановить запись на компьютер">Запись</button>
<script>
document.querySelector('#join').onclick=()=>{document.querySelector('#join').hidden=true;document.querySelector('#leave').hidden=false};
document.querySelector('#more').onclick=()=>{menu.hidden=!menu.hidden};
document.querySelector('#begin').onclick=()=>beginRecording();
document.querySelector('#end').onclick=()=>endRecording();
let audio, dest, wave, recorder, chunks;
window.starts=0;
async function beginRecording(){
  if(Date.now()<(window.__probeIgnoreRestartUntil||0))return;
  if(!audio){
    audio=new AudioContext(); dest=audio.createMediaStreamDestination();
    wave=audio.createOscillator();wave.frequency.value=440;wave.connect(dest);wave.start();
    await audio.resume();
  }
  const nativeStream=new MediaStream(dest.stream.getTracks().map(t=>t.clone()));
  recorder=new MediaRecorder(nativeStream,{mimeType:'audio/webm;codecs=opus'});
  chunks=[]; recorder.ondataavailable=e=>chunks.push(e.data);
  recorder.onstop=()=>{
    nativeStream.getTracks().forEach(t=>t.stop());
    const a=document.createElement('a'); a.download='native.webm';
    a.href=URL.createObjectURL(new Blob(chunks,{type:'audio/webm'}));a.click();
  };
  recorder.start(); window.starts++;
  begin.hidden=true;end.hidden=false;menu.hidden=true;
}
function endRecording(){
  recorder.stop();end.hidden=true;begin.hidden=false;
  if(window.__probeCancelRetry)window.__probeIgnoreRestartUntil=Date.now()+10000;
}
if(window.__probeCloseTrap){
 window.__probeCloseTrapClicks=0;
 const unrelated=document.createElement('button');unrelated.setAttribute('aria-label','Закрыть окно');
 unrelated.textContent='Закрыть окно';unrelated.onclick=()=>{window.__probeCloseTrapClicks++;if(recorder?.state==='recording')endRecording()};document.body.append(unrelated);
}
document.addEventListener('keydown',e=>{if(e.key==='Escape')menu.hidden=true});
</script>'''


def check_audio(path, minimum_seconds=4):
    """Detect silent frames at the boundaries, not just container duration."""
    duration, silent_run, max_silence, samples = 0.0, 0.0, 0.0, 0
    with av.open(str(path)) as media:
        for frame in media.decode(audio=0):
            values = memoryview(frame.planes[0]).cast('f')[:frame.samples]
            rms = math.sqrt(sum(value * value for value in values) / len(values))
            seconds = frame.samples / frame.sample_rate
            duration += seconds
            samples += frame.samples
            silent_run = silent_run + seconds if rms < 0.01 else 0.0
            max_silence = max(max_silence, silent_run)
    assert samples and duration > minimum_seconds, 'Audio after the first file is missing'
    assert max_silence < 0.15, 'Audio gap at a native file boundary'
    return {'audio_seconds': round(duration, 3), 'max_silence_seconds': round(max_silence, 3)}


async def case(mode):
    with tempfile.TemporaryDirectory(prefix='rollover-probe-') as temp:
        sid = '00000000-0000-0000-0000-000000000031'
        service.sessions.clear(); service.stops.clear(); service.tasks.clear()
        state = service.sessions[sid] = {
            'id': sid, 'status': 'starting', 'url': 'https://telemost.yandex.ru/j/1234567890',
            'title': 'Synthetic', 'title_from_telemost': False, 'duration_minutes': 1,
            'recording_deadline': time.time() + 1, 'analysis_types': ['tldr'],
            'recording_authorized': True, 'created_at': time.time(),
        }
        service.stops[sid] = asyncio.Event()
        imports = []
        close_clicks = []
        live_pages = []
        clock_offset = [0]
        original = httpx.AsyncClient

        def respond(request):
            if request.method == 'GET':
                return httpx.Response(404)
            imports.append(1)
            return httpx.Response(200, json={'id': 'synthetic-meeting'})

        async def mute(*args, **kwargs):
            return None  # Device muting has a separate probe.

        actual_playwright = async_playwright()
        async with actual_playwright as playwright:
            original_launch = playwright.chromium.launch

            async def launch(**kwargs):
                browser = await original_launch(**kwargs)
                original_context = browser.new_context

                async def new_context(**options):
                    context = await original_context(**options)
                    context.on('page', lambda page: live_pages.append(page))
                    async def route(request):
                        body = '<iframe src="/private-join/test"></iframe>' if '/private-join/' not in request.request.url else HTML
                        headers={}
                        if mode=='strict_csp':
                            body=body.replace('<script>','<script nonce="test-fixture">')
                            headers['Content-Security-Policy']="script-src 'nonce-test-fixture' blob:; object-src 'none'"
                        await request.fulfill(status=200, content_type='text/html; charset=utf-8', body=body, headers=headers)
                    await context.route('**/*', route)
                    await context.add_init_script('window.__agentCheckpointTimeslice = 250')
                    if mode == 'unrelated_close':
                        await context.add_init_script('window.__probeCloseTrap = true')
                    if mode in {'cancel_during_retry','ended_during_retry'}:
                        await context.add_init_script('window.__probeCancelRetry = true')
                    return context
                browser.new_context = new_context
                return browser

            class Manager:
                async def start(self):
                    return playwright

            # Keep ownership of the real driver in this probe's outer context.
            async def keep_driver():
                pass

            async def rotation(*args, **kwargs):
                if mode == 'failure':
                    raise RuntimeError('synthetic rotation failure')
                return await actual_rotate(*args, **kwargs)

            actual_rotate = service.rotate
            async def stop_later():
                while not state.get('recording_started_at'):
                    await asyncio.sleep(0.1)
                await asyncio.sleep(4)
                page=live_pages[0]
                frame=next(child for child in page.frames if '/private-join/' in child.url)
                if mode == 'unrelated_close':
                    close_clicks.append(await frame.evaluate('window.__probeCloseTrapClicks'))
                if mode == 'cancel_during_retry':
                    service.stops[sid].set()
                    return
                if mode == 'ended_during_retry':
                    await frame.evaluate('''() => {
                      const toast=document.createElement('div');toast.className='Orb-Snackbar-Body';
                      toast.textContent='Организатор завершил встречу для всех';document.body.append(toast);
                      setTimeout(()=>toast.remove(),20);
                    }''')
                    return
                if mode == 'native_stop':
                    await frame.evaluate('endRecording()')
                elif mode == 'transient_ui':
                    await frame.evaluate('document.querySelector("#leave").hidden=true')
                elif mode == 'advisory_expired':
                    clock_offset[0]=7205
                elif mode == 'chat':
                    await frame.evaluate('''() => {
                      const chat=document.createElement('div');chat.setAttribute('role','log');
                      chat.innerHTML='<div data-message-id="stop-now" data-timestamp="'+Date.now()+'"><span data-testid="message-text">Ассистент, останови запись</span></div>';
                      document.body.append(chat);
                    }''')
                    return
                await asyncio.sleep(3)
                assert state['status']=='recording', ('Premature departure', mode, state.get('end_reason'))
                assert not page.is_closed(), ('Browser closed during call', mode)
                if mode == 'transient_ui':
                    await frame.evaluate('document.querySelector("#leave").hidden=false')
                await asyncio.sleep(6)
                service.stops[sid].set()

            stopper = asyncio.create_task(stop_later())
            with patch('playwright.async_api.async_playwright', return_value=Manager()), \
                 patch.object(playwright, 'stop', keep_driver), \
                 patch.object(playwright.chromium, 'launch', launch), \
                 patch.object(service, 'DATA', Path(temp)), \
                 patch.object(service, 'time', SimpleNamespace(time=lambda: time.time()+clock_offset[0], monotonic=time.monotonic)), \
                 patch.object(service, 'ROTATE_AFTER_SECONDS', 1), \
                 patch.object(service, 'ROTATION_TIMEOUT_SECONDS', 1), \
                 patch.object(service, 'mute_devices', mute), \
                 patch.object(service, 'rotate', rotation), \
                 patch.object(service.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(respond), **kw)):
                try:
                    await asyncio.wait_for(service.run(sid), timeout=45)
                    if stopper.done():
                        await stopper
                except asyncio.TimeoutError:
                    print(json.dumps({'event':'offline_case_timeout','mode':mode,**{k:state.get(k) for k in ('status','error_code','recording_issue','native_rotations','capture_health')}}),flush=True)
                    raise
                finally:
                    stopper.cancel()
        if state['status'] not in {'uploaded','partial_saved'}:
            print('synthetic_diagnostics', state.get('error_code'), state.get('diagnostic_flags'),
                  (Path(temp)/sid/'controls.json').read_text() if (Path(temp)/sid/'controls.json').exists() else 'no_controls')
        folder = Path(temp) / sid
        if mode == 'failure':
            assert state['status'] == 'uploaded', state.get('error_code')
            assert not state['recording_complete'] and imports == [1]
            assert state['end_reason'] == 'user_stop'
            return {'mode': mode, 'saved_incomplete': True, 'imports': 1}
        assert state['status'] == 'uploaded', state.get('error_code')
        if mode == 'unrelated_close':
            assert close_clicks == [0], ('Unrequested generic close', close_clicks)
        assert state['native_rotations'] >= (0 if mode in {'cancel_during_retry','ended_during_retry'} else 1), state['native_rotations']
        assert state['recording_complete'] == (mode != 'native_stop'), mode
        assert state['capture_discontinuities'] == 0
        assert state['recording_file'] == 'continuous.webm'
        assert imports == [1], 'Multiple meeting imports'
        assert len(list(folder.glob('native-*.webm'))) == state['native_rotations']
        assert (folder / 'recording.webm').is_file()
        audio = check_audio(folder / 'continuous.webm', 3 if mode in {'cancel_during_retry','ended_during_retry'} else 4)
        assert state['end_reason']==('chat_stop' if mode=='chat' else 'meeting_ended' if mode=='ended_during_retry' else 'user_stop')
        return {'mode': mode, 'rotations': state['native_rotations'], 'imports': len(imports), 'end_reason':state['end_reason'], **audio}


async def main():
    results=[]
    for mode in ('normal', 'failure', 'native_stop', 'transient_ui', 'advisory_expired', 'chat','strict_csp','cancel_during_retry','ended_during_retry','unrelated_close'):
        print(json.dumps({'event':'offline_case_started','mode':mode}),flush=True)
        results.append(await case(mode))
    print(json.dumps({'status': 'passed', 'scope': 'offline_synthetic_only', 'cases': results}))


if __name__ == '__main__':
    asyncio.run(main())

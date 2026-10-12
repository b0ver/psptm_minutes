"""Offline regression for native recorder UI. No meetings, network or user data."""
import asyncio
import json
import os

from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from recording_controls import start_native_recording, stop_native_recording


HTML = '''<!doctype html><button aria-label="Выйти из встречи">Выйти</button>
<button aria-label="Ещё" onclick="window.moreClicks++; menu.hidden=!menu.hidden">Ещё</button>
<div id="menu" hidden><button id="start" onclick="begin()">Записать на компьютер</button>
<button id="legacy" hidden>Остановить запись на компьютер</button></div>
<script>
window.moreClicks=0; window.startClicks=0; window.stopClicks=0; window.downloads=0;
function finish(){window.stopClicks++; window.downloads++;}
legacy.onclick=finish;
function begin(){
  window.startClicks++; window.armedAtStart=window.__agentCheckpointArmed===true;
  start.hidden=true; menu.hidden=true;
  if(window.layout==='menu'){legacy.hidden=false; return;}
  if(window.layout==='missing')return;
  setTimeout(()=>{
    const stop=document.createElement('button');
    stop.setAttribute('aria-label','Остановить запись на компьютер');
    stop.textContent='Запись\\nКомпьютер'; stop.onclick=finish; document.body.append(stop);
    // Hidden stale menu text must not hide the visible toolbar control.
    legacy.hidden=false;
  },window.layout==='delayed'?150:0);
}
document.addEventListener('keydown',e=>{if(e.key==='Escape')menu.hidden=true});
</script>'''


async def case(browser, layout):
    page = await browser.new_page()
    await page.set_content('<iframe></iframe>')
    frame = page.frames[1]
    await frame.set_content(HTML)
    await frame.evaluate('(layout)=>{window.layout=layout; document.body.tabIndex=0; document.body.focus()}', layout)
    try:
        await start_native_recording(page, frame)
    except PWTimeout:
        assert layout == 'missing', 'An active recorder was not recognized'
        assert await frame.evaluate('window.stopClicks') == 0
        await page.close()
        return {'layout': layout, 'unconfirmed_start_rejected': True}
    assert layout != 'missing', 'A start click without a stop control was accepted'
    assert await frame.evaluate('window.armedAtStart && window.startClicks===1')
    assert await page.evaluate('window.__agentCheckpointArmed === true')
    if layout != 'menu':
        assert not await frame.get_by_text('Остановить запись на компьютер', exact=True).is_visible()
    assert not await frame.locator('#menu').is_visible()
    leave = frame.get_by_role('button', name='Выйти из встречи', exact=True)
    before = await frame.evaluate('window.moreClicks')
    for reason in ('meeting_ended_or_removed', 'native_recording_stopped'):
        await stop_native_recording(frame, leave, reason)
    assert await frame.evaluate('window.stopClicks') == 0
    await stop_native_recording(frame, leave, 'max_duration')
    assert await frame.evaluate('window.stopClicks===1 && window.downloads===1')
    assert await frame.evaluate('window.moreClicks') == before + (1 if layout == 'menu' else 0)
    await page.close()
    return {'layout': layout, 'start_confirmed': True, 'download_triggered': True}


async def main():
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
            env={**os.environ, 'XDG_CONFIG_HOME': '/tmp/recording-control-probe'},
            args=['--no-sandbox'])
        try:
            results = [await case(browser, layout) for layout in ('toolbar', 'delayed', 'menu', 'missing')]
        finally:
            await browser.close()
    print(json.dumps({'status': 'passed', 'cases': results}))


if __name__ == '__main__':
    asyncio.run(main())

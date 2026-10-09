"""No-network browser regression: camera UI and Telemost participant component observations."""
import asyncio,os
from pathlib import Path
from playwright.async_api import async_playwright
from devices import mute_devices

async def main():
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',env={**os.environ,'XDG_CONFIG_HOME':'/tmp/observer-probe'})
  page=await browser.new_page()
  await page.set_content('''<button aria-label="Выключить камеру" data-testid="turn-off-camera-button" onclick="setTimeout(()=>{this.setAttribute('aria-label','Включить камеру');this.setAttribute('data-testid','turn-on-camera-button')},50)"></button><button aria-label="Выключить микрофон" onclick="this.setAttribute('aria-label','Включить микрофон')"></button><div data-testid="formatted-call-info">План проекта</div><div id="tile" class="Participant_Oan20 Participant_dominant_LGlf-" style="width:100px;height:100px"></div>''')
  await mute_devices([page],require_camera=True)
  assert await page.locator('[data-testid="turn-on-camera-button"]').count()==1
  await mute_devices([page],require_camera=True)  # Must never toggle a muted camera back on.
  await page.evaluate('''()=>{const el=document.querySelector('#tile');el.__reactFiber$fixture={memoizedProps:{displayName:'Иван Петров',audioTrackState:{muted:false},isDominant:true,isModerator:true}};window.__agentRecordingStartedAt=Date.now()-1000;}''')
  await page.evaluate(Path('/agent/meeting_observer.js').read_text())
  await page.evaluate('()=>window.__agentSampleMeeting()')
  metadata=await page.evaluate('()=>window.__agentMeetingObserver')
  assert metadata['title']=='План проекта'
  assert metadata['participants']==[{'name':'Иван Петров','role':'moderator'}]
  assert metadata['turns'][0]['names']==['Иван Петров']
  await page.locator('#tile').evaluate("e=>e.classList.remove('Participant_dominant_LGlf-')")
  await page.evaluate('()=>window.__agentSampleMeeting()')
  metadata=await page.evaluate('()=>window.__agentMeetingObserver')
  assert metadata['turns'][-1]['names']==[]
  await page.evaluate("""()=>{const tile=document.createElement('div');tile.className='root_ypmDo rootStroke_Kb2PJ';tile.style.cssText='width:100px;height:100px';const label=document.createElement('div');label.className='participantName_tl1zI';label.__reactFiber$fixture={memoizedProps:{displayName:'Анна Смирнова',isAudioOn:true}};tile.append(label);document.body.append(tile);} """)
  await page.evaluate('()=>window.__agentSampleMeeting()')
  metadata=await page.evaluate('()=>window.__agentMeetingObserver')
  assert metadata['turns'][-1]['names']==['Анна Смирнова']
  assert any(p['name']=='Анна Смирнова' for p in metadata['participants'])
  await browser.close()
 print('camera_off_and_metadata_browser_probe_passed')
asyncio.run(main())

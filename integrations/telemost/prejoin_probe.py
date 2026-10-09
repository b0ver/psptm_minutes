"""Read-only guest-screen check; never presses the join button or starts recording."""
import asyncio,json,os,shutil
from pathlib import Path
from playwright.async_api import async_playwright
from devices import mute_devices

async def main():
 shutil.copytree('/agent/nssdb','/home/recorder/.pki/nssdb',dirs_exist_ok=True)
 state=max([json.loads(p.read_text()) for p in Path('/data/telemost').glob('*/state.json')],key=lambda s:s.get('created_at',0))
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',env={**os.environ,'XDG_CONFIG_HOME':'/tmp/prejoin-probe'},args=['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream'])
  context=await browser.new_context(locale='ru-RU');await context.grant_permissions(['microphone','camera'])
  page=await context.new_page();await page.goto(state['url'],wait_until='domcontentloaded',timeout=60000)
  try:await page.get_by_text('Продолжить в браузере',exact=True).click(timeout=10000)
  except Exception:pass
  frame=None
  for _ in range(40):
   frame=next((f for f in page.frames if '/private-join/' in f.url),None)
   if frame and await frame.locator('input[type=text]').is_visible():break
   await asyncio.sleep(1)
  if not frame:raise RuntimeError('guest_frame_not_found')
  assert await mute_devices([page,frame],require_camera=True)
  assert await frame.locator('[data-testid="turn-on-camera-button"]').is_visible()
  assert await frame.locator('[data-testid="turn-on-mic-button"]').is_visible()
  await mute_devices([page,frame],require_camera=True)
  assert await frame.locator('[data-testid="turn-on-camera-button"]').is_visible()
  await browser.close()
 print('live_telemost_prejoin_camera_and_mic_off_confirmed_no_join')
asyncio.run(main())

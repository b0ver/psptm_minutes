"""Verify that a read-only CDP observer preserves the owner's downloads.

Run the default observer contrast only in an isolated synthetic container.
"""
import asyncio,json,tempfile,os,sys
from pathlib import Path
from playwright.async_api import async_playwright
async def trial(p,no_defaults):
 with tempfile.TemporaryDirectory() as temp:
  root=Path(temp);downloads=root/'downloads';downloads.mkdir()
  ctx=await p.chromium.launch_persistent_context(str(root/'profile'),executable_path='/usr/bin/chromium',headless=True,downloads_path=str(downloads),accept_downloads=True,env={**os.environ,'XDG_CONFIG_HOME':str(root/'config')},args=['--remote-debugging-port=9222'])
  observer=None
  try:
   page=ctx.pages[0];await page.route('**/*',lambda route:route.fulfill(status=200,body='<title>Synthetic download</title>',content_type='text/html'));await page.goto('https://fixture.invalid/')
   observer=await p.chromium.connect_over_cdp('http://127.0.0.1:9222',no_defaults=no_defaults)
   async with page.expect_download(timeout=5000) as event:
    await page.evaluate("()=>{const a=document.createElement('a');a.download='synthetic.webm';a.href=URL.createObjectURL(new Blob(['synthetic bytes']));document.body.append(a);a.click()}")
   download=await event.value;failure=await download.failure();saved=False
   try:
    target=root/'saved.webm';await asyncio.wait_for(download.save_as(str(target)),5);saved=target.read_bytes()==b'synthetic bytes'
   except Exception:pass
   return {'no_defaults':no_defaults,'owner_save_succeeded':saved,'protocol_download_failed':bool(failure)}
  finally:
   if observer:await observer.close()
   await ctx.close()
async def main():
 async with async_playwright() as p:
  flags=(False,True) if '--contrast-default' in sys.argv else (True,)
  result=[await trial(p,flag) for flag in flags]
  assert result[-1]['owner_save_succeeded'] and not result[-1]['protocol_download_failed']
  print(json.dumps({'status':'passed','cases':result}))
asyncio.run(main())

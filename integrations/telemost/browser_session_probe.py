"""Verify on-disk storage, guest isolation, and scoped profile removal."""
import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from playwright.async_api import async_playwright
from browser_session import launch_session, remove_browser_data

async def main():
    async with async_playwright() as p:
        with TemporaryDirectory() as temp:
            first, second = Path(temp) / 'first', Path(temp) / 'second'
            context = await launch_session(p.chromium, first)
            async def page_for(ctx):
                await ctx.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body='<title>Storage fixture</title>'))
                page = ctx.pages[0]
                await page.goto('https://fixture.invalid/')
                return page
            page = await page_for(context)
            await page.evaluate('''async()=>{
                localStorage.setItem('marker','first');
                await new Promise((ok,no)=>{
                    const r=indexedDB.open('session-1',1);
                    r.onupgradeneeded=()=>r.result.createObjectStore('chunks');
                    r.onerror=()=>no(r.error);
                    r.onsuccess=()=>{const db=r.result;const tx=db.transaction('chunks','readwrite');
                      tx.objectStore('chunks').put(new Blob(['private synthetic bytes']),1);
                      tx.oncomplete=()=>{db.close();ok()};tx.onabort=()=>no(tx.error)};
                });
            }''')
            await context.close()
            assert any((first/'browser-profile'/'Default'/'IndexedDB').rglob('*.log')), 'No disk-backed database log'
            reopened = await p.chromium.launch_persistent_context(str(first/'browser-profile'), headless=True, executable_path=os.environ.get('CHROMIUM_EXECUTABLE_PATH'), env={**os.environ,'XDG_CONFIG_HOME':str(first/'browser-config'),'XDG_CACHE_HOME':str(first/'browser-cache')})
            try:
                page = await page_for(reopened)
                assert await page.evaluate("localStorage.getItem('marker')") == 'first'
                assert await page.evaluate('''async()=>{
                    const db=await new Promise((ok,no)=>{const r=indexedDB.open('session-1');r.onsuccess=()=>ok(r.result);r.onerror=()=>no(r.error)});
                    const blob=await new Promise((ok,no)=>{const r=db.transaction('chunks').objectStore('chunks').get(1);r.onsuccess=()=>ok(r.result);r.onerror=()=>no(r.error)});
                    db.close();return blob.text();
                }''') == 'private synthetic bytes'
            finally:
                await reopened.close()
            isolated = await launch_session(p.chromium, second)
            try:
                page = await page_for(isolated)
                assert await page.evaluate("localStorage.getItem('marker')") is None
                assert await page.evaluate('(async()=> (await indexedDB.databases()).length)()') == 0
            finally:
                await isolated.close()
            (first/'recording.webm').write_bytes(b'keep-recording')
            (first/'checkpoints').mkdir()
            (first/'checkpoints'/'manifest.json').write_text('{}')
            remove_browser_data(first)
            assert (first/'recording.webm').read_bytes() == b'keep-recording'
            assert (first/'checkpoints'/'manifest.json').exists()
            assert not (first/'browser-profile').exists()
            assert (second/'browser-profile').is_dir()
            print(json.dumps({'status':'passed','disk_storage_survives_close':True,'session_isolated':True,'recording_retained':True}))

if __name__ == '__main__':
    asyncio.run(main())

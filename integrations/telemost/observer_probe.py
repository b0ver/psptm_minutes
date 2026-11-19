"""Verify that a passive CDP observer preserves the owner's downloads.

Use --contrast-default only in a separate synthetic container.
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from playwright.async_api import async_playwright


async def trial(playwright, no_defaults):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        downloads = root / 'downloads'
        downloads.mkdir()
        context = await playwright.chromium.launch_persistent_context(
            str(root / 'profile'), executable_path='/usr/bin/chromium',
            headless=True, downloads_path=str(downloads), accept_downloads=True,
            env={**os.environ, 'XDG_CONFIG_HOME': str(root / 'config')},
            args=['--remote-debugging-port=9222'])
        observer = None
        try:
            page = context.pages[0]
            await page.route('**/*', lambda route: route.fulfill(
                status=200, body='<title>Synthetic download</title>', content_type='text/html'))
            await page.goto('https://fixture.invalid/')

            async def owned_download(name):
                result = {'event_received': False, 'save_succeeded': False,
                          'browser_reported_failure': None}
                try:
                    async with page.expect_download(timeout=5000) as event:
                        await page.evaluate('''name=>{
                            const a=document.createElement('a');a.download=name;
                            a.href=URL.createObjectURL(new Blob(['synthetic bytes']));
                            document.body.append(a);a.click();
                        }''', name)
                    download = await event.value
                    result['event_received'] = True
                    result['browser_reported_failure'] = bool(await download.failure())
                    target = root / name
                    await asyncio.wait_for(download.save_as(str(target)), 5)
                    result['save_succeeded'] = target.read_bytes() == b'synthetic bytes'
                except Exception:
                    pass  # The explicit contrast may deliberately break ownership.
                return result

            observer = await playwright.chromium.connect_over_cdp(
                'http://127.0.0.1:9222', no_defaults=no_defaults)
            connected = await owned_download('connected.webm')
            await observer.close()
            observer = None
            detached = await owned_download('detached.webm')
            return {'no_defaults': no_defaults, 'connected': connected, 'detached': detached}
        finally:
            if observer:
                await observer.close()
            await context.close()


async def main():
    async with async_playwright() as playwright:
        flags = (False, True) if '--contrast-default' in sys.argv else (True,)
        results = [await trial(playwright, flag) for flag in flags]
        for stage in ('connected', 'detached'):
            assert results[-1][stage]['save_succeeded'], results[-1]
            assert results[-1][stage]['browser_reported_failure'] is False, results[-1]
        print(json.dumps({'status': 'passed', 'cases': results}))


if __name__ == '__main__':
    asyncio.run(main())

"""Offline synthetic audio canary. Run only in a disposable, network-disabled container."""
import asyncio
import json
import os
import signal
import tempfile
from pathlib import Path

from playwright.async_api import async_playwright
from checkpoints import Checkpoints, CheckpointError


async def case(driver, mode):
    with tempfile.TemporaryDirectory(prefix='checkpoint-probe-') as folder:
        store = Checkpoints(folder)
        browser = await driver.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
            env={**os.environ, 'XDG_CONFIG_HOME':'/tmp/probe-config', 'XDG_CACHE_HOME':'/tmp/probe-cache'},
            args=['--autoplay-policy=no-user-gesture-required'])
        context = await browser.new_context()
        async def receive(source, packet):
            return await asyncio.to_thread(store.accept, packet)
        await context.expose_binding('agentCheckpoint', receive)
        script = Path(__file__).with_name('checkpoint.js').read_text()
        await context.add_init_script(script)
        page = await context.new_page()
        await page.evaluate(script)  # Installing twice must not duplicate capture.
        await page.evaluate('''mode => {
          window.__agentCheckpointArmed = mode !== 'unarmed';
          window.__agentCheckpointTimeslice = 1000;
          const audio = window.audio = new AudioContext();
          const dest = audio.createMediaStreamDestination();
          const wave = audio.createOscillator(); wave.frequency.value = 440;
          wave.connect(dest); wave.start(); audio.resume();
          window.nativeChunks = 0;
          window.recorder = new MediaRecorder(dest.stream, {mimeType:'audio/webm;codecs=opus'});
          recorder.ondataavailable = () => window.nativeChunks++;
          recorder.start();
        }''', mode)
        if mode == 'unarmed':
            await page.wait_for_timeout(1500)
            await page.evaluate('recorder.stop()')
            await page.wait_for_function('window.nativeChunks === 1')
            assert store.summary()['chunks'] == 0
            await browser.close()
            return {'mode':mode, 'chunks':0}
        await page.wait_for_function('window.__agentCheckpoint.chunks >= 3', timeout=15000)
        assert await page.evaluate('window.nativeChunks') == 0, 'Mirror changed native recorder cadence'
        if mode == 'crash':
            # This disposable container has exactly one root Chromium process.
            roots = []
            for entry in Path('/proc').iterdir():
                if not entry.name.isdecimal():
                    continue
                try:
                    command = (entry/'cmdline').read_bytes().split(b'\0')
                except OSError:
                    continue
                if command and command[0] == b'/usr/lib/chromium/chromium' and not any(x.startswith(b'--type=') for x in command):
                    roots.append(int(entry.name))
            assert len(roots) == 1, 'Cannot identify isolated browser process'
            closed = asyncio.create_task(page.wait_for_event('close', timeout=10000))
            await asyncio.sleep(0)
            os.kill(roots[0], signal.SIGKILL)
            await closed
        else:
            await page.evaluate('recorder.pause()')
            assert await page.evaluate('window.__agentCheckpoint.state') == 'recording'
            await page.evaluate('recorder.resume(); recorder.stop()')
            await page.wait_for_function('window.__agentCheckpoint.state === "final"', timeout=10000)
            assert await page.evaluate('window.nativeChunks') == 1
        reloaded = Checkpoints(folder)
        recovered = reloaded.assemble(Path(folder)/'recovered.webm')
        assert recovered['recovered_audio_seconds'] > 2.0
        assert recovered['checkpoint_final'] == (mode == 'normal')
        # Corruption must never be accepted as a valid recovery.
        part = reloaded.folder/'000000.part'
        part.write_bytes(part.read_bytes()+b'corruption')
        try:
            reloaded.assemble(Path(folder)/'corrupt.webm')
            raise AssertionError('Corruption was accepted')
        except CheckpointError:
            pass
        await browser.close()
        return {'mode':mode, **recovered}


async def main():
    async with async_playwright() as driver:
        results = [await case(driver, mode) for mode in ('unarmed', 'normal', 'crash')]
    print(json.dumps({'status':'passed', 'cases':results}))


if __name__ == '__main__':
    asyncio.run(main())

"""One private disk-backed Chromium profile per recording session."""
import os
import shutil
from pathlib import Path

DIRECTORIES = ('browser-profile', 'browser-downloads', 'browser-config', 'browser-cache')

async def launch_session(chromium, folder):
    folder = Path(folder)
    for name in DIRECTORIES:
        (folder / name).mkdir(mode=0o700, parents=True, exist_ok=False)
    return await chromium.launch_persistent_context(
        str(folder / 'browser-profile'), headless=True,
        downloads_path=str(folder / 'browser-downloads'),
        env={**os.environ, 'XDG_CONFIG_HOME': str(folder / 'browser-config'),
             'XDG_CACHE_HOME': str(folder / 'browser-cache')},
        executable_path=os.environ.get('CHROMIUM_EXECUTABLE_PATH'),
        locale='ru-RU', accept_downloads=True, viewport={'width': 1280, 'height': 900},
        args=['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
              '--use-file-for-fake-audio-capture=/agent/silence.wav',
              '--autoplay-policy=no-user-gesture-required', '--disable-dev-shm-usage'])


def remove_browser_data(folder):
    # Call only after a confirmed browser close and successful complete import.
    # Recording files and checkpoints are outside these fixed subdirectories.
    for name in DIRECTORIES:
        target = Path(folder) / name
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)

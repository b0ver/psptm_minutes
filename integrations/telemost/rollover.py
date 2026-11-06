"""Rotate native recordings before Chrome's 30 minute limit.

Only an agent-requested rotation can keep the authorized audio copy alive.
An unsolicited native stop still ends the recording.
"""
import asyncio
import time

ROTATE_AFTER_SECONDS = 29 * 60
ROTATION_TIMEOUT_SECONDS = 45


async def prepare_stop(page):
    for scope in page.frames:
        await scope.evaluate('() => window.__agentPrepareStop?.()')


async def rotate(page, frame, leave, stop, deadline, start_native, stop_native):
    """Return False if cancellation/deadline/removal prevents another part."""
    if stop.is_set() or time.time() >= deadline or not await leave.is_visible():
        return False
    armed = [await scope.evaluate('() => window.__agentBeginRollover?.() || false')
             for scope in page.frames]
    if not any(armed):
        raise RuntimeError('continuous_capture_unavailable')

    async def change_part():
        await stop_native(frame, leave, 'native_rotation')
        if stop.is_set() or time.time() >= deadline or not await leave.is_visible():
            await prepare_stop(page)
            return False
        if not any([await scope.evaluate('() => window.__agentCanContinueRollover?.() || false') for scope in page.frames]):
            return False  # A participant's stop must not become our restart.
        started = await start_native(page, frame)
        return started is not False

    return await asyncio.wait_for(change_part(), timeout=ROTATION_TIMEOUT_SECONDS)

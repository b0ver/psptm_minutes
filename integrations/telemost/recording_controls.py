"""Native recorder controls across Telemost toolbar and menu layouts."""
import asyncio
import time
try:
    from .states import CONTROLS
except ImportError:
    from states import CONTROLS


def stop_recording_control(frame):
    # The toolbar exposes the stop command through aria-label while its visible
    # text is only “Запись / Компьютер”. Older layouts expose menu text instead.
    return frame.get_by_role('button', name=CONTROLS['stop_recording'], exact=True).or_(
        frame.get_by_text(CONTROLS['stop_recording'], exact=True)
    ).filter(visible=True).first


async def start_native_recording(page, frame, should_stop=None, restart=False):
    from playwright.async_api import TimeoutError as PWTimeout
    cancelled = should_stop or (lambda: False)
    if cancelled():
        return False
    more = frame.locator('button[aria-label="Ещё"]').last
    record = frame.get_by_text(CONTROLS['start_recording'], exact=True).filter(visible=True).first
    if not await record.is_visible():
        await more.evaluate('(button) => button.click()')
    deadline = time.monotonic() + 15
    while not await record.is_visible():
        if cancelled():
            return False
        if time.monotonic() >= deadline:
            raise PWTimeout('native_start_command_unavailable')
        await asyncio.sleep(.1)
    for scope in page.frames:
        await scope.evaluate('() => {window.__agentCheckpointArmed = true; window.__agentExpectedRecordingVideo = true}')
    stop = stop_recording_control(frame)
    deadline = time.monotonic() + (60 if restart else 20)
    attempts, next_attempt, revealed_after_attempt = 0, 0, 0
    while time.monotonic() < deadline:
        active = await frame.evaluate('() => window.__agentNativeRecordingActive')
        if await stop.is_visible() and active is not False:
            await page.keyboard.press('Escape')
            return True
        if cancelled():
            return False
        if restart and active is not True and await record.is_visible() and not await record.is_enabled():
            raise PWTimeout('native_recording_disabled')
        now = time.monotonic()
        # Telemost takes only the leading start action while the previous
        # recording finishes saving. Retry the visible UI command, never a
        # recorder API or a disabled/revoked control.
        if active is not True and attempts < (30 if restart else 5) and now >= next_attempt and await record.is_visible() and await record.is_enabled():
            await record.evaluate('(element) => element.click()')
            attempts += 1
            next_attempt = now + 2
        elif attempts and revealed_after_attempt != attempts and not await record.is_visible() and not await stop.is_visible() and now >= next_attempt - 1:
            if cancelled():
                return False
            # Every attempted menu command can close the menu, even when
            # Telemost's leading worker is still busy with the previous file.
            await more.evaluate('(button) => button.click()')
            revealed_after_attempt = attempts
        await asyncio.sleep(.1)
    raise PWTimeout('native_recording_start_not_confirmed')


async def stop_native_recording(frame, leave, end_reason):
    if end_reason in {'meeting_ended_or_removed', 'native_recording_stopped', 'meeting_ended', 'removed'}:
        return
    if await frame.evaluate('() => window.__agentNativeRecordingActive') is False:
        return  # Cancellation may arrive between native files.
    if await leave.is_visible():
        stop = stop_recording_control(frame)
        if not await stop.is_visible():
            await frame.locator('button[aria-label="Ещё"]').last.evaluate('(button) => button.click()')
        await stop.wait_for(state='visible', timeout=15000)
        await stop.evaluate('(element, rotation) => {if (rotation) window.__agentConfirmNativeRotationStop?.(); element.click()}', end_reason == 'native_rotation')
        await stop.wait_for(state='hidden', timeout=15000)

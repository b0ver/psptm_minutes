"""Native recorder controls across Telemost toolbar and menu layouts."""
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


async def start_native_recording(page, frame):
    more = frame.locator('button[aria-label="Ещё"]').last
    await more.evaluate('(button) => button.click()')
    record = frame.get_by_text(CONTROLS['start_recording'], exact=True)
    await record.wait_for(state='visible', timeout=15000)
    for scope in page.frames:
        await scope.evaluate('() => {window.__agentCheckpointArmed = true}')
    await record.evaluate('(element) => element.click()')
    stop = stop_recording_control(frame)
    if not await stop.is_visible():
        await more.evaluate('(button) => button.click()')
    # Confirm the actual stop command; a successful start click is insufficient.
    await stop.wait_for(state='visible', timeout=20000)
    await page.keyboard.press('Escape')


async def stop_native_recording(frame, leave, end_reason):
    if end_reason in {'meeting_ended_or_removed', 'native_recording_stopped'}:
        return
    if await leave.is_visible():
        stop = stop_recording_control(frame)
        if not await stop.is_visible():
            await frame.locator('button[aria-label="Ещё"]').last.evaluate('(button) => button.click()')
        await stop.wait_for(state='visible', timeout=15000)
        await stop.evaluate('(element) => element.click()')

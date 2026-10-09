"""Explicit UI mute, independent of track state and onboarding overlays."""
async def mute_devices(scopes,require_camera=False):
    camera_off=False
    for scope in scopes:
        if require_camera and scope is scopes[-1]:
            known=scope.locator('[data-testid="turn-off-camera-button"], [data-testid="turn-on-camera-button"], [data-testid="organizer-revoked-camera-permission-button"], button[aria-label="Выключить камеру"], button[aria-label="Включить камеру"]')
            await known.first.wait_for(state='visible',timeout=5000)
        clicked_camera=False
        for label,testid in (('Выключить камеру','turn-off-camera-button'),('Выключить микрофон','turn-off-mic-button')):
            controls=scope.locator('button[aria-label="'+label+'"],button[data-testid="'+testid+'"]')
            for i in range(await controls.count()):
                control=controls.nth(i)
                if await control.is_visible():
                    await control.evaluate('(button)=>button.click()')
                    if testid=='turn-off-camera-button':clicked_camera=True
        # Defensive safeguard for streams created after the pre-join mute.
        await scope.evaluate('() => (window.__agentStreams || []).forEach(s => s.getTracks().forEach(t => {t.enabled=false}))')
        off=scope.locator('[data-testid="turn-on-camera-button"], [data-testid="organizer-revoked-camera-permission-button"], button[aria-label="Включить камеру"]')
        if clicked_camera or await off.count():
            try:await off.first.wait_for(state='visible',timeout=3000);camera_off=True
            except Exception:pass
    if require_camera and not camera_off:raise RuntimeError('camera_mute_not_confirmed')
    return camera_off

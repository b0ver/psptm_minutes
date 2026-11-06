"""Secondary MediaRecorders must not own or finalize authorized capture."""
import asyncio,json,os
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'],env={**os.environ,'XDG_CONFIG_HOME':'/tmp/checkpoint-scope'})
  try:
   page=await browser.new_page()
   await page.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html',body='<title>Synthetic capture test</title>'))
   await page.goto('https://fixture.invalid/')
   packets=[]
   await page.expose_binding('agentCheckpoint',lambda source,packet:packets.append(packet))
   await page.add_script_tag(content=Path('/agent/checkpoint.js').read_text())
   await page.evaluate('''async()=>{
    window.__agentCheckpointArmed=true;window.__agentCheckpointTimeslice=100;
    window.audio=new AudioContext();window.dest=audio.createMediaStreamDestination();
    const wave=audio.createOscillator();wave.connect(dest);wave.start();await audio.resume();
    window.primary=new MediaRecorder(dest.stream);primary.start();
   }''')
   await asyncio.sleep(.3)
   await page.evaluate('''()=>{window.secondary=new MediaRecorder(dest.stream);secondary.start();secondary.pause()}''')
   before=len(packets)
   await asyncio.sleep(.5)
   assert len(packets)>=before+2,'Secondary pause paused the primary capture'
   assert not await page.evaluate('window.__agentRecordingHadPause === true')
   await page.evaluate('secondary.resume();secondary.stop()')
   await asyncio.sleep(.5)
   state=await page.evaluate('()=>({capture:window.__agentCheckpoint.state,nativeActive:window.__agentNativeRecordingActive,primary:primary.state})')
   assert state=={'capture':'recording','nativeActive':True,'primary':'recording'},state
   assert not any(packet.get('final') for packet in packets),'Secondary recorder finalized capture'
   storage_errors=await page.evaluate('''async()=>{
    const db=await new Promise((ok,no)=>{const r=indexedDB.open('session-1',1);r.onupgradeneeded=()=>r.result.createObjectStore('chunks');r.onsuccess=()=>ok(r.result);r.onerror=()=>no(r.error)});
    await new Promise((ok,no)=>{const tx=db.transaction('chunks','readwrite');tx.objectStore('chunks').add(new Blob(['first']),1);tx.oncomplete=ok;tx.onerror=no});
    await new Promise(ok=>{const tx=db.transaction('chunks','readwrite');tx.objectStore('chunks').add(new Blob(['duplicate']),1);tx.onabort=ok});
    // Synchronous failures must propagate unchanged and be observable.
    try {db.transaction('missing-store')} catch(e) {if(e.name!=='NotFoundError')throw e}
    const tx=db.transaction('chunks','readwrite');
    try {tx.objectStore('chunks').put(()=>{},2)} catch(e) {if(e.name!=='DataCloneError')throw e}
    await new Promise(ok=>{tx.oncomplete=ok;tx.onabort=ok});
    // Explicit cancellation must not add a storage-fault code.
    const cancelled=db.transaction('chunks','readwrite');cancelled.abort();
    await new Promise(ok=>{cancelled.onabort=ok});
    db.close();return window.__agentContinuousCapture.storageErrors;
   }''')
   assert set(storage_errors)=={'ConstraintError','NotFoundError','DataCloneError'},storage_errors
   await page.add_script_tag(content='window.stopPrimaryFromFixture=()=>primary.stop();\n//# sourceURL=https://fixture.invalid/static/recorder-fixture.js')
   await page.evaluate('stopPrimaryFromFixture()')
   await page.wait_for_function('()=>window.__agentCheckpoint.state==="final"')
   diagnostic=await page.evaluate('window.__agentContinuousCapture.nativeStopDiagnostic')
   site=diagnostic.pop('callsite')
   assert diagnostic=={'trigger':'page_stop','error':'none','pagehide':False},diagnostic
   assert site['script']=='recorder-fixture.js' and site['line']>0 and site['column']>0,site
   assert set(site)=={'script','line','column'},site
   assert sum(bool(packet.get('final')) for packet in packets)==1
   print(json.dumps({'status':'passed','secondary_stop_ignored':True,'primary_stop_finalized_once':True,'storage_error_code_observed':True}))
  finally:await browser.close()

if __name__=='__main__':asyncio.run(main())

"""Secondary MediaRecorders must not own or finalize authorized capture."""
import asyncio,json,os
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'],env={**os.environ,'XDG_CONFIG_HOME':'/tmp/checkpoint-scope'})
  try:
   page=await browser.new_page()
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
   await page.evaluate('''()=>{window.secondary=new MediaRecorder(dest.stream);secondary.start();secondary.stop()}''')
   await asyncio.sleep(.5)
   state=await page.evaluate('()=>({capture:window.__agentCheckpoint.state,nativeActive:window.__agentNativeRecordingActive,primary:primary.state})')
   assert state=={'capture':'recording','nativeActive':True,'primary':'recording'},state
   assert not any(packet.get('final') for packet in packets),'Secondary recorder finalized capture'
   await page.evaluate('primary.stop()')
   await page.wait_for_function('()=>window.__agentCheckpoint.state==="final"')
   assert sum(bool(packet.get('final')) for packet in packets)==1
   print(json.dumps({'status':'passed','secondary_stop_ignored':True,'primary_stop_finalized_once':True}))
  finally:await browser.close()

if __name__=='__main__':asyncio.run(main())

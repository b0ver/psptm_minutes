"""Synthetic DOM contract, not an assertion about current Telemost selectors."""
import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright


async def case(browser, mode):
    page=await browser.new_page()
    await page.set_content('<h1 id="heading">Встреча</h1><div role="log" id="chat"></div>')
    await page.evaluate(Path(__file__).with_name('lifecycle.js').read_text())
    if mode == 'history':
        await page.evaluate('''() => {chat.innerHTML='<div data-message-id="old"><span data-testid="message-text">/stop</span></div>'}''')
    if mode != 'unarmed_end':
        await page.evaluate("authority => {window.__agentArmCommands();window.__agentArmLifecycle(authority)}",
                            'shell' if mode.startswith('shell_') else 'chat' if mode=='unrelated_card' else 'meeting')
    await page.wait_for_timeout(10)
    await page.evaluate('''mode => {
      if(['removed','ended','shell_heading','plain_heading'].includes(mode)){
        heading.textContent=mode==='removed'?'Вас исключили из встречи':'Встреча завершена';return;
      }
      if(mode==='shell_alert'){
        const alert=document.createElement('div');alert.setAttribute('role','alert');
        alert.textContent='Встреча завершена';document.body.append(alert);return;
      }
      if(['current_ended','brief_ended','shell_toast','shell_generic_toast'].includes(mode)){
        const toast=document.createElement('div');toast.className='Orb-Snackbar-Body';
        toast.textContent=mode==='shell_generic_toast'?'Встреча завершена':'Организатор завершил встречу для всех';document.body.append(toast);
        if(mode==='brief_ended')setTimeout(()=>toast.remove(),20);return;
      }
      if(mode==='call_title'){
        const leave=document.createElement('button');leave.setAttribute('aria-label','Выйти из встречи');
        leave.textContent='Выйти';document.body.append(leave);heading.textContent='Встреча завершена';return;
      }
      if(['current_removed','current_gone','shell_card','unrelated_card','active_card','unarmed_end','shell_live_card'].includes(mode)){
        heading.remove();const card=document.createElement('div');card.className='container_XvwZc';
        const title=document.createElement('div');title.className='title_dsYpg';
        title.textContent=mode==='current_removed'?'Вас удалили из этой встречи':'Эта встреча уже закончилась,\\nно вы можете создать новую';
        card.append(title);document.body.append(card);
        if(mode==='shell_live_card'){
          const frame=document.createElement('iframe');frame.src='/private-join/live';document.body.append(frame);
        }
        if(mode==='active_card'){
          const leave=document.createElement('button');leave.setAttribute('aria-label','Выйти из встречи');
          leave.textContent='Выйти';document.body.append(leave);
        }
        return;
      }
      if(mode==='transient'){document.querySelector('h1').remove();return;}
      if(mode==='history'){chat.firstChild.setAttribute('data-timestamp',Date.now());return;}
      const node=document.createElement('div');node.setAttribute('data-message-id','new');
      if(mode!=='unknown_time')node.setAttribute('data-timestamp',Date.now()-(mode==='old_time'?60000:0));
      node.innerHTML=mode==='quote'?'<blockquote><span data-testid="message-text">/stop</span></blockquote>':
        mode==='heading_in_chat'?'<h2>Встреча завершена</h2>':
        '<span data-testid="message-text">'+(mode==='ordinary'?'Обсудим /stop завтра':'Ассистент, останови запись')+'</span>';
      chat.append(node);
    }''',mode)
    if mode=='brief_ended':await page.wait_for_timeout(100)
    result=await page.evaluate('window.__agentSampleLifecycle()')
    expected={'command':'chat_stop','shell_card':'meeting_ended','shell_toast':'meeting_ended',
              'current_ended':'meeting_ended','current_removed':'removed','current_gone':'meeting_ended','brief_ended':'meeting_ended'}.get(mode)
    assert result['reason']==expected,(mode,result)
    await page.close()
    return {'case':mode,'reason':result['reason']}


async def transport_case(browser, mode):
    """Real MessageChannel, cross-origin frames, preserved message delivery."""
    context=await browser.new_context()
    calls=[]
    await context.expose_binding('agentLifecycle',lambda source, reason:calls.append(reason))
    await context.add_init_script(path=str(Path(__file__).with_name('lifecycle.js')))
    async def route(request):
        body='<iframe src="https://telemost.360.yandex.ru/private-join/probe"></iframe>' if '/private-join/' not in request.request.url else '<h1>Fixture call</h1>'
        await request.fulfill(status=200,content_type='text/html',body=body)
    await context.route('**/*',route)
    page=await context.new_page()
    parent='https://other.invalid/probe' if mode=='port_wrong_parent' else 'https://telemost.yandex.ru/probe'
    await page.goto(parent)
    child=next(frame for frame in page.frames if '/private-join/' in frame.url)
    if mode!='port_unarmed':await child.evaluate('window.__agentArmLifecycle("meeting")')
    await child.evaluate("""() => {
      addEventListener('message',e=>{if(e.data?.type==='init-connection')window.__probePort=e.ports[0]});
    }""")
    packet={'event':'DISCONNECTED','payload':{'id':'self','source':{
        'port_kick':'kick','port_unknown':'unknown','port_sleep':'sleep','port_bad_source':'__proto__'
    }.get(mode,'close-room')}}
    if mode=='port_other_peer':packet['payload']['id']='other'
    if mode=='port_connected':packet['event']='CONNECTED'
    if mode=='port_json':packet=json.dumps(packet)
    if mode=='port_bad_json':packet='{'
    if mode=='port_brief_end':await page.evaluate('window.__probeClose=true')
    await page.evaluate("""packet => {
      const channel=new MessageChannel();window.__probeDelivered=false;
      channel.port1.onmessage=e=>{window.__probeDelivered=JSON.stringify(e.data)===JSON.stringify(packet);if(window.__probeClose)document.querySelector('iframe').remove()};
      document.querySelector('iframe').contentWindow.postMessage({type:'init-connection',name:'messenger'},
        'https://telemost.360.yandex.ru',[channel.port2]);
    }""",packet)
    await child.wait_for_function('window.__probePort')
    if mode=='port_untracked':
        await child.evaluate('window.__probePort=new MessageChannel().port1')
    try:
        await child.evaluate('packet=>window.__probePort.postMessage(packet)',packet)
    except Exception:
        if mode!='port_brief_end' or not child.is_detached():raise
    if mode!='port_untracked':await page.wait_for_function('window.__probeDelivered===true')
    await page.wait_for_timeout(100)
    expected='removed' if mode=='port_kick' else 'meeting_ended' if mode in {'port_end','port_json','port_brief_end'} else None
    assert calls==([expected] if expected else []),(mode,calls)
    if mode=='port_unknown':
        assert (await child.evaluate('window.__agentLifecycle.presence'))=='unconfirmed'
    if mode=='port_connected':
        assert (await child.evaluate('window.__agentLifecycle.presence'))=='connected'
    await context.close()
    return {'case':mode,'reason':expected,'delivery_preserved':mode!='port_untracked'}


async def window_transport_case(browser, mode):
    context=await browser.new_context()
    calls=[]
    await context.expose_binding('agentLifecycle',lambda source, reason:calls.append(reason))
    await context.add_init_script(path=str(Path(__file__).with_name('lifecycle.js')))
    parent='https://telemost.yandex.ru/probe-root'
    host='untrusted.invalid' if mode=='window_bad_origin' else 'telemost.360.yandex.ru'
    async def route(request):
        body=(f'<iframe src="https://{host}/private-join/probe"></iframe>'
              '<iframe src="https://telemost.360.yandex.ru/unrelated"></iframe>') if request.request.url==parent else '<h1>Fixture</h1>'
        await request.fulfill(status=200,content_type='text/html',body=body)
    await context.route('**/*',route)
    page=await context.new_page();await page.goto(parent)
    await page.evaluate('window.__agentArmLifecycle("shell")')
    child=next(frame for frame in page.frames if ('/unrelated' if mode=='window_wrong_sender' else '/private-join/') in frame.url)
    packet={'event':'DISCONNECTED','payload':{'id':'other' if mode=='window_other_peer' else 'self',
            'source':'kick' if mode=='window_kick' else 'close-room'}}
    if mode=='window_json':packet=json.dumps(packet)
    await child.evaluate('packet=>parent.postMessage(packet,"https://telemost.yandex.ru")',packet)
    await page.wait_for_timeout(100)
    expected='removed' if mode=='window_kick' else 'meeting_ended' if mode in {'window_end','window_json'} else None
    assert calls==([expected] if expected else []),(mode,calls)
    await context.close()
    return {'case':mode,'reason':expected}


async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='/usr/bin/chromium',
            env={**os.environ, 'XDG_CONFIG_HOME':'/tmp/lifecycle-probe-config',
                 'XDG_CACHE_HOME':'/tmp/lifecycle-probe-cache'})
        try:
            cases=[await case(browser,mode) for mode in ('history','old_time','unknown_time','quote','heading_in_chat','ordinary','transient','command','ended','removed','current_ended','current_removed','current_gone','call_title','brief_ended','unarmed_end','shell_heading','shell_alert','shell_toast','shell_card','unrelated_card','plain_heading','active_card','shell_live_card','shell_generic_toast')]
            cases.extend([await transport_case(browser,mode) for mode in ('port_end','port_kick','port_unknown','port_sleep','port_other_peer','port_unarmed','port_wrong_parent','port_untracked','port_bad_source','port_json','port_bad_json','port_connected','port_brief_end')])
            cases.extend([await window_transport_case(browser,mode) for mode in ('window_end','window_kick','window_json','window_bad_origin','window_wrong_sender','window_other_peer')])
        finally:
            await browser.close()
    print(json.dumps({'status':'passed','scope':'synthetic_dom_only','cases':cases}))


if __name__=='__main__':
    asyncio.run(main())

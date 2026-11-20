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


async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='/usr/bin/chromium',
            env={**os.environ, 'XDG_CONFIG_HOME':'/tmp/lifecycle-probe-config',
                 'XDG_CACHE_HOME':'/tmp/lifecycle-probe-cache'})
        try:
            cases=[await case(browser,mode) for mode in ('history','old_time','unknown_time','quote','heading_in_chat','ordinary','transient','command','ended','removed','current_ended','current_removed','current_gone','call_title','brief_ended','unarmed_end','shell_heading','shell_alert','shell_toast','shell_card','unrelated_card','plain_heading','active_card','shell_live_card','shell_generic_toast')]
        finally:
            await browser.close()
    print(json.dumps({'status':'passed','scope':'synthetic_dom_only','cases':cases}))


if __name__=='__main__':
    asyncio.run(main())

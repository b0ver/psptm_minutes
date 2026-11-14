// Read only explicit end screens and addressed stop commands. Never persist chat.
(() => {
  if (window.__agentLifecycle) return;
  const result = window.__agentLifecycle = {reason: null, chat_available: false};
  let armedAt = null;
  const seen = new Set();
  const messages = () => Array.from(document.querySelectorAll(
    '[role="log"] [data-message-id], [role="log"] [data-messageid], ' +
    '[data-testid="chat-messages"] [data-testid="chat-message"], ' +
    '[class*="messagesList"] [data-message-id], [class*="MessageList"] [data-message-id]'));
  const normalize = text => text.trim().toLocaleLowerCase('ru-RU').replace(/ё/g, 'е').replace(/\s+/g, ' ');
  const commands = new Set(['/stop', 'ассистент, останови запись', 'ассистент, заверши запись',
    'ассистент протокола, останови запись']);
  const endScreens = new Map([
    ['встреча завершена', 'meeting_ended'], ['звонок завершен', 'meeting_ended'],
    ['организатор завершил встречу', 'meeting_ended'], ['вас исключили из встречи', 'removed'],
    ['организатор завершил встречу для всех', 'meeting_ended'],
    ['эта встреча уже закончилась, но вы можете создать новую', 'meeting_ended'],
    ['вас удалили из этой встречи', 'removed'],
    ['вы исключены из встречи', 'removed'], ['вас удалили из встречи', 'removed']]);
  function signal(reason) {
    result.reason = reason;
    if (typeof window.agentLifecycle === 'function') Promise.resolve(window.agentLifecycle(reason)).catch(() => {});
    return result;
  }
  function sample() {
    if (result.reason) return result;
    // Chat text or a temporary missing toolbar is never proof of removal.
    const inMeeting = Array.from(document.querySelectorAll('button[aria-label="Выйти из встречи"]')).some(n => n.getBoundingClientRect().width);
    for (const node of document.querySelectorAll('[role="alert"], [role="heading"], h1, h2, [data-testid="call-ended"], .container_XvwZc .title_dsYpg, .Orb-Snackbar-Body')) {
      if (node.closest('[role="log"], [data-testid="chat-messages"], [class*="messagesList"], [class*="MessageList"], .yamb-chat') || !node.getBoundingClientRect().width) continue;
      if (inMeeting && node.matches('h1, h2, [role="heading"]') && !node.closest('.container_XvwZc')) continue;
      const reason = endScreens.get(normalize(node.textContent).replace(/[.!]$/, ''));
      if (reason) return signal(reason);
    }
    const nodes = messages();
    result.chat_available = Boolean(document.querySelector('[role="log"], [data-testid="chat-messages"], [class*="messagesList"], [class*="MessageList"]'));
    if (armedAt === null) return result;
    for (const node of nodes) {
      const id = node.getAttribute('data-message-id') || node.getAttribute('data-messageid') || node.id;
      if (!id || seen.has(id)) continue;
      seen.add(id);
      // Reject history, edits, forwarded text and unknown timestamps.
      const raw = node.getAttribute('data-timestamp') || node.querySelector('time[datetime]')?.getAttribute('datetime');
      let stamp = raw && (/^\d+$/.test(raw) ? Number(raw) : Date.parse(raw));
      if (stamp && stamp < 1e12) stamp *= 1000;
      if (!stamp || stamp < armedAt || stamp > Date.now() + 10000 || node.querySelector('blockquote, pre, code, [data-forwarded]')) continue;
      const textNode = node.querySelector('[data-testid="message-text"], [class*="messageText"]');
      if (textNode && commands.has(normalize(textNode.textContent))) return signal('chat_stop');
    }
    return result;
  }
  window.__agentArmCommands = () => {
    if (armedAt !== null) return;
    armedAt = Date.now();
    for (const node of messages()) {
      const id = node.getAttribute('data-message-id') || node.getAttribute('data-messageid') || node.id;
      if (id) seen.add(id);
    }
  };
  window.__agentSampleLifecycle = sample;
  // Remember a short end notification even while Python awaits a download or
  // recorder control. Pass only the stable reason code to the session owner.
  new MutationObserver(sample).observe(document, {subtree: true, childList: true, characterData: true});
})();

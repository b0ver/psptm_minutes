// Observe the visible call UI only. No account data, chat messages or credentials.
(() => {
  if (window.__agentMeetingObserver) return;
  const result = window.__agentMeetingObserver = {title: null, participants: [], turns: [], source: 'telemost_ui'};
  const roster = new Map();
  function props(element) {
    const key = Object.keys(element).find(k => k.startsWith('__reactFiber$'));
    let fiber = key ? element[key] : null;
    for (let n=0; fiber && n<12; n++, fiber=fiber.return) {
      const p=fiber.memoizedProps;
      if (p && typeof p.displayName==='string' && (p.audioTrackState || typeof p.isDominant==='boolean' || typeof p.isAudioOn==='boolean')) return p;
    }
    return null;
  }
  function sample() {
    const label=document.querySelector('[data-testid="formatted-call-info"]');
    const title=label?.textContent?.trim();
    if (title && title.length<=200 && !/^(Встреча|Звонок|Meeting)\s*№?\s*\d+$/i.test(title)) result.title=title;
    const active=[];
    const candidates=new Set(Array.from(document.querySelectorAll('[class]')).filter(tile=>Array.from(tile.classList).some(c=>/^Participant_[A-Za-z0-9-]+$/.test(c))));
    for (const label of document.querySelectorAll('[class*="participantName_"]')) {
      const tile=label.closest('[class*="root_"]');
      if (tile) candidates.add(tile);
    }
    for (const tile of candidates) {
      if (!tile.getBoundingClientRect().width || !tile.getBoundingClientRect().height) continue;
      const p=props(tile) || props(tile.querySelector('[class*="participantName_"]')||tile),name=p?.displayName?.trim();
      if (!name || name.length>200 || name.startsWith('Ассистент протокола')) continue;
      let role=p.isModerator ? 'moderator' : p.isOwner ? 'organizer' : p.isSharingScreen ? 'presenter' : 'participant';
      roster.set(name,{name,role});
      const highlighted=Array.from(tile.classList).some(c=>c.startsWith('Participant_dominant_')||c.startsWith('rootStroke_'));
      if (highlighted && !p.audioTrackState?.muted && p.isAudioOn!==false) active.push(name);
    }
    result.participants=Array.from(roster.values()).slice(0,100);
    const origin=window.__agentRecordingStartedAt;
    if (!origin || result.turns.length>=20000) return;
    const now=Math.max(0,(Date.now()-origin)/1000),names=Array.from(new Set(active)).sort();
    const last=result.turns[result.turns.length-1];
    if (last && JSON.stringify(last.names)===JSON.stringify(names) && now-last.end<1.5) last.end=now;
    else result.turns.push({start:now,end:now,names});
  }
  window.__agentSampleMeeting=sample;
  const timer=setInterval(sample,500);
  window.__agentStopMeetingObserver=()=>{clearInterval(timer);sample();};
})();

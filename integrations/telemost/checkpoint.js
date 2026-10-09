// Mirror only a natively authorized recording's audio stream. No RTCPeerConnection patch.
(() => {
  if (window.__agentCheckpointInstalled || !window.MediaRecorder) return;
  window.__agentCheckpointInstalled = true;
  const Native = window.MediaRecorder;
  let mirror, chain = Promise.resolve(), sequence = 0, failed = false;
  const streamId = Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
  window.__agentCheckpoint = {state: 'idle', chunks: 0, pending: 0};
  const publish = packet => {
    window.__agentCheckpoint.pending++;
    chain = chain.then(() => window.agentCheckpoint(packet)).then(() => {
      window.__agentCheckpoint.pending--;
      window.__agentCheckpoint.lastAck = Date.now();
    }).catch(() => {
      failed = true; window.__agentCheckpoint.state = 'failed';
      window.__agentCheckpoint.pending = 0;
      if (mirror && mirror.state !== 'inactive') mirror.stop();
    });
  };
  class ObservedRecorder extends Native {
    start(...args) {
      super.start(...args);
      if (!window.__agentCheckpointArmed || mirror || !this.stream.getAudioTracks().length) return;
      window.__agentRecordingStartedAt=Date.now();
      try {
        mirror = new Native(new MediaStream(this.stream.getAudioTracks()), {mimeType: 'audio/webm;codecs=opus', audioBitsPerSecond: 128000});
        mirror.ondataavailable = event => {
          if (!event.data.size || failed) return;
          const n = sequence++;
          // Serialize conversion AND durable delivery: no reordered acknowledgements.
          window.__agentCheckpoint.pending++;
          chain = chain.then(async () => {
            const bytes = new Uint8Array(await event.data.arrayBuffer());
            let binary = '';
            for (let offset = 0; offset < bytes.length; offset += 16384) binary += String.fromCharCode(...bytes.subarray(offset, offset + 16384));
            await window.agentCheckpoint({stream: streamId, sequence: n, mime: mirror.mimeType, data: btoa(binary)});
            window.__agentCheckpoint.chunks++;
            window.__agentCheckpoint.pending--;
            window.__agentCheckpoint.lastAck = Date.now();
          }).catch(() => {failed = true; window.__agentCheckpoint.state = 'failed'; window.__agentCheckpoint.pending = 0; if (mirror.state !== 'inactive') mirror.stop();});
        };
        mirror.onstop = () => {
          if (failed) return;
          publish({stream: streamId, sequence, final: true});
          chain = chain.then(() => {if (!failed) window.__agentCheckpoint.state = 'final';});
        };
        mirror.onerror = () => {failed = true; window.__agentCheckpoint.state = 'failed';};
        this.addEventListener('stop', () => {if (mirror.state !== 'inactive') mirror.stop()}, {once: true});
        mirror.start(window.__agentCheckpointTimeslice || 10000);
        window.__agentCheckpoint.state = 'recording';
        window.__agentFinalizeCheckpoint = () => {if (mirror.state !== 'inactive') mirror.stop(); return chain;};
      } catch { window.__agentCheckpoint.state = 'unsupported'; }
    }
    pause() {
      super.pause();
      window.__agentRecordingHadPause=true;
      if (mirror?.state === 'recording') mirror.pause();
    }
    resume() {
      super.resume();
      if (mirror?.state === 'paused') mirror.resume();
    }
    stop() {
      super.stop();
      if (mirror && mirror.state !== 'inactive') mirror.stop();
    }
  }
  window.MediaRecorder = ObservedRecorder;
})();

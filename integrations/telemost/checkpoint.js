// Mirror only a natively authorized recording's audio stream. No RTCPeerConnection patch.
(() => {
  if (window.__agentCheckpointInstalled || !window.MediaRecorder) return;
  window.__agentCheckpointInstalled = true;
  const Native = window.MediaRecorder;
  let mirror, chain = Promise.resolve(), sequence = 0, failed = false;
  let audioContext, destination, input, inputStream, rotating = false, stopping = false;
  let generation = 0, activeNative;
  const capture = window.__agentContinuousCapture = {rotations: 0, discontinuities: 0};
  const releaseInput = () => {
    input?.disconnect();
    inputStream?.getTracks().forEach(track => track.stop());
  };
  const connectInput = stream => {
    // Clones survive a native recorder stopping its own tracks. Keep one
    // output stream and one MediaRecorder across every native file boundary.
    const previousGeneration = ++generation;
    releaseInput();
    inputStream = new MediaStream(stream.getAudioTracks().map(track => track.clone()));
    inputStream.getTracks().forEach(track => track.addEventListener('ended', () => {
      if (!stopping && previousGeneration === generation) capture.discontinuities++;
    }));
    input = audioContext.createMediaStreamSource(inputStream);
    input.connect(destination);
  };
  window.__agentBeginRollover = () => {
    if (stopping || failed || !mirror || mirror.state !== 'recording' || !audioContext || audioContext.state !== 'running') return false;
    rotating = true;
    return true;
  };
  window.__agentPrepareStop = () => {stopping = true; rotating = false;};
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
      if (!window.__agentCheckpointArmed || !this.stream.getAudioTracks().length) return;
      activeNative = this;
      window.__agentNativeRecordingActive = true;
      const onNativeStop = () => {
        if (this === activeNative) window.__agentNativeRecordingActive = false;
        if (this === activeNative && !rotating && mirror?.state !== 'inactive') mirror?.stop();
      };
      this.addEventListener('stop', onNativeStop, {once: true});
      if (mirror) {
        if (!rotating || stopping || failed || mirror.state !== 'recording') return;
        connectInput(this.stream);
        rotating = false;
        capture.rotations++;
        return;
      }
      window.__agentRecordingStartedAt=Date.now();
      try {
        audioContext = new AudioContext();
        destination = audioContext.createMediaStreamDestination();
        audioContext.addEventListener('statechange', () => {
          if (!stopping && audioContext.state !== 'running') capture.discontinuities++;
        });
        connectInput(this.stream);
        mirror = new Native(destination.stream, {mimeType: 'audio/webm;codecs=opus', audioBitsPerSecond: 128000});
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
          if (failed) {
            stopping = true;
            releaseInput();
            audioContext.close();
            return;
          }
          publish({stream: streamId, sequence, final: true});
          chain = chain.then(() => {
            if (!failed) window.__agentCheckpoint.state = 'final';
            stopping = true;
            releaseInput();
            audioContext.close();
          });
        };
        mirror.onerror = () => {failed = true; window.__agentCheckpoint.state = 'failed';};
        mirror.start(window.__agentCheckpointTimeslice || 10000);
        window.__agentCheckpoint.state = 'recording';
        window.__agentFinalizeCheckpoint = () => {stopping = true; rotating = false; if (mirror.state !== 'inactive') mirror.stop(); return chain;};
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
    }
  }
  window.MediaRecorder = ObservedRecorder;
})();

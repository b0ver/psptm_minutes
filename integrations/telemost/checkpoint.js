// Mirror only a natively authorized recording's audio stream. No RTCPeerConnection patch.
(() => {
  if (window.__agentCheckpointInstalled || !window.MediaRecorder) return;
  window.__agentCheckpointInstalled = true;
  const Native = window.MediaRecorder;
  let mirror, chain = Promise.resolve(), sequence = 0, failed = false;
  let audioContext, destination, input, inputStream, rotating = false, stopping = false;
  let generation = 0, activeNative, requestedNativeStop = false;
  let nativeStopCommand, nativeStopCallsite, nativeError, pagehideSeen = false;
  window.addEventListener('pagehide', () => {pagehideSeen = true;});
  const capture = window.__agentContinuousCapture = {rotations: 0, discontinuities: 0};
  const nativeDestinations = new Map(), nativeConnections = new WeakMap(), heldInputs = new Map();
  let nativeAudioTrackId, nativeDestination;
  const nodeConnect = window.AudioNode?.prototype.connect;
  const nodeDisconnect = window.AudioNode?.prototype.disconnect;
  capture.preservedNativeDisconnects = 0;
  capture.heldNativeInputs = 0;
  if (window.AudioNode && window.MediaStreamAudioDestinationNode && window.WeakRef) {
    AudioNode.prototype.connect = function(...args) {
      const result = nodeConnect.apply(this, args);
      // Telemost connects each meeting source directly to its recording mixer.
      if (args.length === 1 && args[0] instanceof MediaStreamAudioDestinationNode) {
        const target = args[0];
        const linked = nativeConnections.get(this) || new Set();
        linked.add(target); nativeConnections.set(this, linked);
        for (const track of target.stream.getAudioTracks()) {
          nativeDestinations.set(track.id, new WeakRef(target));
          if (track.id === nativeAudioTrackId) nativeDestination = target;
        }
      }
      return result;
    };
    AudioNode.prototype.disconnect = function(...args) {
      const linked = nativeConnections.get(this);
      const intentionalStop = rotating && requestedNativeStop && nativeStopCommand === 'agent_rotation';
      const preserve = args.length === 0 && intentionalStop && !stopping && !failed && mirror?.state === 'recording' && activeNative?.state === 'inactive' && linked?.has(nativeDestination);
      const result = nodeDisconnect.apply(this, args);
      if (args.length === 0 || args[0] === 0) linked?.clear();
      else if (args[0] instanceof MediaStreamAudioDestinationNode) linked?.delete(args[0]);
      if (preserve) {
        // Other outputs are disconnected normally. Keep only the old native
        // mixer fed until the next authorized recorder has actually started.
        nodeConnect.call(this, nativeDestination);
        linked.add(nativeDestination);
        const targets = heldInputs.get(this) || new Set();
        if (!targets.has(nativeDestination)) capture.preservedNativeDisconnects++;
        targets.add(nativeDestination); heldInputs.set(this, targets);
        capture.heldNativeInputs = heldInputs.size;
      }
      return result;
    };
  }
  const releaseNativeBridge = () => {
    for (const [node, targets] of heldInputs) for (const target of targets) {
      try {nodeDisconnect.call(node, target);} catch {}
      nativeConnections.get(node)?.delete(target);
    }
    heldInputs.clear(); capture.heldNativeInputs = 0;
  };
  // Observe only Telemost recording databases. Keep codes, never records or text.
  const storageErrors = new Set();
  capture.storageErrors = [];
  const observeStorageError = error => {
    const name = error?.name;
    const code = ['QuotaExceededError', 'UnknownError', 'DataError', 'DataCloneError', 'AbortError', 'ConstraintError', 'InvalidStateError', 'TransactionInactiveError', 'NotFoundError', 'ReadOnlyError'].includes(name) ? name : 'other';
    storageErrors.add(code);
    capture.storageErrors = [...storageErrors];
    if (/Failed to write blobs|FILE_ERROR_NO_SPACE/.test(error?.message || '')) capture.blobWriteFailed = true;
  };
  const isRecordingDatabase = db => /^session-[0-9]+$/.test(db?.name || '');
  if (window.IDBFactory) {
    const openDatabase = IDBFactory.prototype.open;
    IDBFactory.prototype.open = function(...args) {
      const request = openDatabase.apply(this, args);
      if (/^session-[0-9]+$/.test(String(args[0]))) request.addEventListener('success', () => {
        for (const type of ['error', 'abort']) request.result.addEventListener(type, event => {
          const error = event.target?.error;
          // Explicit abort() has no error. It is not evidence of a storage fault.
          if (error) observeStorageError(error);
        }, true);
      });
      return request;
    };
    const transaction = IDBDatabase.prototype.transaction;
    IDBDatabase.prototype.transaction = function(...args) {
      try {return transaction.apply(this, args);} catch (error) {
        if (isRecordingDatabase(this)) observeStorageError(error);
        throw error;
      }
    };
    const put = IDBObjectStore.prototype.put;
    IDBObjectStore.prototype.put = function(...args) {
      try {return put.apply(this, args);} catch (error) {
        if (isRecordingDatabase(this.transaction.db)) observeStorageError(error);
        throw error;
      }
    };
  }
  const releaseInput = () => {
    input?.disconnect();
    inputStream?.getTracks().forEach(track => track.stop());
  };
  const connectInput = stream => {
    // Clones survive a native recorder stopping its own tracks. Keep one
    // output stream and one MediaRecorder across every native file boundary.
    const previousGeneration = ++generation;
    nativeAudioTrackId = stream.getAudioTracks()[0]?.id;
    nativeDestination = nativeDestinations.get(nativeAudioTrackId)?.deref();
    for (const [id, ref] of nativeDestinations) if (!ref.deref()) nativeDestinations.delete(id);
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
    requestedNativeStop = false;
    return true;
  };
  window.__agentConfirmNativeRotationStop = () => {if (rotating && !stopping) requestedNativeStop = true;};
  window.__agentCanContinueRollover = () => rotating && requestedNativeStop && !nativeError && !failed && !stopping && mirror?.state === 'recording';
  window.__agentPrepareStop = () => {stopping = true; rotating = false; requestedNativeStop = false; releaseNativeBridge();};
  window.__agentAbortRollover = () => {
    rotating = false; requestedNativeStop = false; releaseNativeBridge();
    if (!activeNative || activeNative.state === 'inactive') {
      stopping = true;
      if (mirror && mirror.state !== 'inactive') mirror.stop();
    }
    return chain;
  };
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
      if (window.__agentExpectedRecordingVideo && !this.stream.getVideoTracks().length) return;
      // Diagnostics and other recorders on the page do not own this capture.
      // Only an explicitly armed rollover may replace the primary recorder.
      if (mirror && (!rotating || stopping || failed || mirror.state !== 'recording')) return;
      activeNative = this;
      nativeStopCommand = undefined;
      nativeStopCallsite = undefined;
      nativeError = undefined;
      this.addEventListener('error', event => {
        if (this !== activeNative) return;
        const name = event.error?.name;
        nativeError = ['UnknownError', 'InvalidModificationError', 'SecurityError', 'NotSupportedError', 'OutOfMemoryError'].includes(name) ? name : 'other';
      });
      window.__agentNativeRecordingActive = true;
      const onNativeStop = () => {
        if (this === activeNative) {
          window.__agentNativeRecordingActive = false;
          capture.nativeStopDiagnostic = {trigger: nativeStopCommand || 'native_event', error: nativeError || 'none', pagehide: pagehideSeen, callsite: nativeStopCallsite || null};
        }
        if (this === activeNative && !(rotating && requestedNativeStop && nativeStopCommand === 'agent_rotation')) {
          rotating = false; requestedNativeStop = false; releaseNativeBridge();
          if (mirror?.state !== 'inactive') mirror?.stop();
        }
      };
      this.addEventListener('stop', onNativeStop, {once: true});
      if (mirror) {
        connectInput(this.stream);
        releaseNativeBridge();
        rotating = false;
        requestedNativeStop = false;
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
          releaseNativeBridge();
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
        mirror.onerror = () => {failed = true; releaseNativeBridge(); window.__agentCheckpoint.state = 'failed';};
        mirror.start(window.__agentCheckpointTimeslice || 10000);
        window.__agentCheckpoint.state = 'recording';
        window.__agentFinalizeCheckpoint = () => {window.__agentPrepareStop(); if (mirror.state !== 'inactive') mirror.stop(); return chain;};
      } catch { window.__agentCheckpoint.state = 'unsupported'; }
    }
    pause() {
      super.pause();
      if (this !== activeNative) return;
      window.__agentRecordingHadPause=true;
      capture.discontinuities++;
      if (mirror?.state === 'recording') mirror.pause();
    }
    resume() {
      super.resume();
      if (this !== activeNative) return;
      if (mirror?.state === 'paused') mirror.resume();
    }
    stop() {
      if (this === activeNative) {
        nativeStopCommand = rotating && requestedNativeStop ? 'agent_rotation' : stopping ? 'agent_stop' : 'page_stop';
        // Retain only a public JS filename and numeric source coordinates.
        // Do not retain URLs, paths, function arguments, or the stack itself.
        const site = String(new Error().stack || '').split('\n').map(line => line.match(/\b([A-Za-z0-9._-]{1,120}\.js):(\d+):(\d+)\)?$/)).find(site => site && site[1] !== 'checkpoint.js');
        nativeStopCallsite = site ? {script: site[1], line: Number(site[2]), column: Number(site[3])} : undefined;
      }
      super.stop();
    }
  }
  window.MediaRecorder = ObservedRecorder;
})();

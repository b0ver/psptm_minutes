"""Durable ordered fragments of ONE WebM stream, never independent clips."""
import base64
import hashlib
import copy
import json
import os
import re
import time
from pathlib import Path


class CheckpointError(ValueError):
    pass


def atomic(path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('wb') as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    tmp.replace(path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Checkpoints:
    def __init__(self, folder):
        self.folder = Path(folder) / 'checkpoints'
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.folder / 'manifest.json'
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {'stream': None, 'parts': [], 'final': False}

    def accept(self, packet):
        stream = packet.get('stream', '')
        if not re.fullmatch('[a-f0-9]{32}', stream):
            raise CheckpointError('invalid_stream')
        if self.state['stream'] not in (None, stream):
            raise CheckpointError('multiple_capture_streams')
        next_state = copy.deepcopy(self.state)
        next_state['stream'] = stream
        seq = packet.get('sequence')
        if type(seq) is not int or seq < 0:
            raise CheckpointError('invalid_sequence')
        if packet.get('final'):
            if seq != len(self.state['parts']):
                raise CheckpointError('final_sequence_gap')
            next_state['final'] = True
        else:
            if not str(packet.get('mime', '')).startswith('audio/webm'):
                raise CheckpointError('unsupported_checkpoint_format')
            encoded = packet.get('data', '')
            if not isinstance(encoded, str) or len(encoded) > 6 * 1024 * 1024:
                raise CheckpointError('checkpoint_too_large')
            try:
                data = base64.b64decode(encoded, validate=True)
            except ValueError:
                raise CheckpointError('invalid_checkpoint_data') from None
            if not data or (seq == 0 and not data.startswith(bytes.fromhex('1a45dfa3'))):
                raise CheckpointError('missing_webm_header')
            digest = hashlib.sha256(data).hexdigest()
            if seq < len(self.state['parts']):
                if self.state['parts'][seq]['sha256'] != digest:
                    raise CheckpointError('checkpoint_conflict')
                return self.summary()
            if self.state['final'] or seq != len(self.state['parts']):
                raise CheckpointError('checkpoint_sequence_gap')
            atomic(self.folder / f'{seq:06d}.part', data)
            next_state['parts'].append({'sequence': seq, 'bytes': len(data), 'sha256': digest})
        next_state['last_ack_at'] = time.time()
        atomic(self.path, json.dumps(next_state).encode())
        self.state = next_state
        return self.summary()

    def summary(self):
        return {'chunks': len(self.state['parts']), 'checkpoint_bytes': sum(p['bytes'] for p in self.state['parts']), 'checkpoint_final': self.state['final'], 'last_checkpoint_at': self.state.get('last_ack_at')}

    def assemble(self, target):
        if not self.state['parts']:
            raise CheckpointError('no_saved_chunks')
        tmp = Path(target).with_suffix('.tmp')
        with tmp.open('wb') as out:
            for part in self.state['parts']:
                data = (self.folder / f"{part['sequence']:06d}.part").read_bytes()
                if hashlib.sha256(data).hexdigest() != part['sha256']:
                    tmp.unlink(missing_ok=True)
                    raise CheckpointError('checkpoint_corrupt')
                out.write(data)
            out.flush()
            os.fsync(out.fileno())
        # Decode a prefix after a crash; fragments after gaps are never joined.
        import av
        samples, rate = 0, 0
        try:
            with av.open(str(tmp), options={'protocol_whitelist': 'file,pipe'}) as media:
                if not media.streams.audio:
                    raise CheckpointError('checkpoint_has_no_audio')
                for frame in media.decode(audio=0):
                    samples += frame.samples
                    rate = frame.sample_rate
        except CheckpointError:
            tmp.unlink(missing_ok=True)
            raise
        except av.error.FFmpegError:
            tmp.unlink(missing_ok=True)
            raise CheckpointError('checkpoint_decode_failed') from None
        if not samples or not rate:
            tmp.unlink(missing_ok=True)
            raise CheckpointError('checkpoint_has_no_samples')
        tmp.replace(target)
        return {'recovered_audio_seconds': round(samples / rate, 3), **self.summary()}

import base64

import pytest
from integrations.telemost.checkpoints import Checkpoints, CheckpointError

STREAM = 'a' * 32
HEADER = bytes.fromhex('1a45dfa3') + b'synthetic'


def packet(n, data=HEADER):
    return {'stream': STREAM, 'sequence': n, 'mime': 'audio/webm;codecs=opus', 'data': base64.b64encode(data).decode()}


def test_ordered_durable_ack_and_replay(tmp_path):
    store = Checkpoints(tmp_path)
    assert store.accept(packet(0))['chunks'] == 1
    assert Checkpoints(tmp_path).accept(packet(0))['chunks'] == 1
    store.accept(packet(1, b'cluster'))
    assert Checkpoints(tmp_path).summary()['chunks'] == 2
    store.accept({'stream': STREAM, 'sequence': 2, 'final': True})
    assert Checkpoints(tmp_path).summary()['checkpoint_final'] is True
    with pytest.raises(CheckpointError):
        store.accept(packet(2, b'late'))


def test_gaps_conflicts_and_missing_header_rejected(tmp_path):
    store = Checkpoints(tmp_path)
    with pytest.raises(CheckpointError): store.accept(packet(1))
    with pytest.raises(CheckpointError): store.accept(packet(0, b'no header'))
    store.accept(packet(0))
    with pytest.raises(CheckpointError): store.accept(packet(0, HEADER+b'changed'))
    with pytest.raises(CheckpointError): store.accept({'stream': STREAM, 'sequence': 2, 'final': True})
    with pytest.raises(CheckpointError): store.accept({**packet(1), 'stream': 'b'*32})
    assert store.summary()['chunks'] == 1


def test_failed_manifest_write_can_be_retried(tmp_path, monkeypatch):
    from integrations.telemost import checkpoints
    store = Checkpoints(tmp_path)
    original = checkpoints.atomic
    def fail_manifest(path, data):
        if path.name=='manifest.json': raise OSError('synthetic disk error')
        return original(path,data)
    monkeypatch.setattr(checkpoints,'atomic',fail_manifest)
    with pytest.raises(OSError): store.accept(packet(0))
    assert store.summary()['chunks']==0
    monkeypatch.setattr(checkpoints,'atomic',original)
    assert store.accept(packet(0))['chunks']==1
    assert Checkpoints(tmp_path).summary()['chunks']==1

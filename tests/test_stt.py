from types import SimpleNamespace

import pytest

from app import stt
from app.ai import ProcessingError


def weights(tmp_path, monkeypatch):
    for name in ('model.bin','config.json','tokenizer.json'):
        (tmp_path/name).write_text('fixture')
    monkeypatch.setattr(stt,'MODEL_PATH',tmp_path)


def test_cuda_missing_fails_before_loading_weights(tmp_path,monkeypatch):
    weights(tmp_path,monkeypatch)
    monkeypatch.setenv('STT_DEVICE','cuda')
    monkeypatch.setenv('STT_COMPUTE_TYPE','float16')
    monkeypatch.setattr('ctranslate2.get_cuda_device_count',lambda:0)
    monkeypatch.setattr('faster_whisper.WhisperModel',lambda *a,**k:pytest.fail('Must not load model or fall back to CPU'))
    with pytest.raises(ProcessingError,match='stt_cuda_unavailable'):
        stt.create_model()


def test_requested_precision_must_be_supported(tmp_path,monkeypatch):
    weights(tmp_path,monkeypatch)
    monkeypatch.setenv('STT_DEVICE','cuda')
    monkeypatch.setenv('STT_COMPUTE_TYPE','float16')
    monkeypatch.setattr('ctranslate2.get_cuda_device_count',lambda:1)
    monkeypatch.setattr('ctranslate2.get_supported_compute_types',lambda d:{'float32'})
    with pytest.raises(ProcessingError,match='stt_compute_type_unsupported'):
        stt.create_model()


def test_model_is_released_when_decoding_raises(tmp_path,monkeypatch):
    from app import worker
    unloaded=[]
    class Model:
        def __init__(self):
            self.model=SimpleNamespace(unload_model=lambda:unloaded.append(True))
        def transcribe(self,*a,**k):
            raise RuntimeError('synthetic decoder failure')
    monkeypatch.setattr(worker,'create_model',Model)
    monkeypatch.setattr('app.media.decode_local',lambda p:'synthetic audio')
    with pytest.raises(RuntimeError):
        worker.transcribe(tmp_path/'test.wav')
    assert unloaded==[True]

import wave

import numpy as np
import pytest

from app.media import decode_local
from app.ai import ProcessingError


def test_real_local_wav_decode(tmp_path):
    path = tmp_path / 'test.wav'
    samples = (np.sin(np.arange(16000) * .1) * 2000).astype(np.int16)
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
        out.writeframes(samples.tobytes())
    decoded = decode_local(path)
    assert decoded.dtype == np.float32 and decoded.shape == (16000,)
    assert np.max(np.abs(decoded)) < 1


def test_corrupt_media_has_safe_error(tmp_path):
    path = tmp_path / 'bad.mp4'
    path.write_text('not media')
    with pytest.raises(ProcessingError,match='recording_decode_failed'):
        decode_local(path)

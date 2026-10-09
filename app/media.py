"""Decode local media without allowing FFmpeg network protocols."""
from pathlib import Path

import av
import numpy as np

from app.ai import ProcessingError


def decode_local(path: Path):
    frames, samples = [], 0
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    try:
        with av.open(str(path), options={"protocol_whitelist": "file,pipe"}) as container:
            if not container.streams.audio:
                raise ProcessingError("recording_has_no_audio")
            for frame in container.decode(audio=0):
                for resampled in resampler.resample(frame):
                    arr = resampled.to_ndarray().flatten()
                    samples += arr.size
                    if samples > 2 * 3600 * 16000:
                        raise ProcessingError("recording_exceeds_two_hours")
                    frames.append(arr)
            for resampled in resampler.resample(None):
                frames.append(resampled.to_ndarray().flatten())
    except av.error.FFmpegError:
        raise ProcessingError("recording_decode_failed") from None
    if not frames:
        raise ProcessingError("empty_recording")
    return np.concatenate(frames).astype(np.float32) / 32768.0

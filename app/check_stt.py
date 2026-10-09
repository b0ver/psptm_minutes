"""Offline GPU smoke: load existing weights and decode one second of synthetic silence."""
import json
import sys
import time

import numpy as np

from app.ai import ProcessingError
from app.stt import create_model, release_model


def main():
    model = None
    try:
        start = time.monotonic()
        model = create_model()
        device, compute = model.model.device, model.model.compute_type
        if device != "cuda":
            raise ProcessingError("stt_probe_requires_cuda")
        # VAD off ensures real encoder/decoder CUDA kernels are exercised.
        segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language="ru",
                                       beam_size=1, vad_filter=False,
                                       condition_on_previous_text=False, max_new_tokens=8)
        count = len(list(segments))
        print(json.dumps({"status": "ok", "device": device, "compute_type": compute,
                          "elapsed_seconds": round(time.monotonic() - start, 2),
                          "input": "one_second_synthetic_silence", "segment_count": count,
                          "real_recording_processed": False, "models_downloaded": False}))
        return 0
    except ProcessingError as e:
        code = e.code
    except Exception as e:
        # Exception class is useful for diagnosing libraries without dumping payloads.
        code = "stt_gpu_probe_failed_" + type(e).__name__
    finally:
        if model is not None:
            release_model(model)
    print(json.dumps({"status": "failed", "code": code}))
    return 1


if __name__ == "__main__":
    sys.exit(main())

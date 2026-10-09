import os

from app.ai import ProcessingError
from app.settings import MODEL_PATH


def create_model():
    if not MODEL_PATH.is_dir() or not all((MODEL_PATH / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json")):
        raise ProcessingError("stt_weights_missing")
    device = os.getenv("STT_DEVICE", "cpu")
    compute = os.getenv("STT_COMPUTE_TYPE", "float16" if device == "cuda" else "int8")
    if device not in ("cpu", "cuda"):
        raise ProcessingError("stt_device_invalid")
    from faster_whisper import WhisperModel
    import ctranslate2
    if device == "cuda" and ctranslate2.get_cuda_device_count() < 1:
        raise ProcessingError("stt_cuda_unavailable")
    if compute not in ctranslate2.get_supported_compute_types(device):
        raise ProcessingError("stt_compute_type_unsupported")
    # Explicit device selection; no automatic fallback or model downloads.
    return WhisperModel(str(MODEL_PATH), device=device, device_index=0,
                        compute_type=compute, local_files_only=True, cpu_threads=4)


def release_model(model):
    # Release STT weights before the next local Ollama stage uses the same GPU.
    model.model.unload_model()

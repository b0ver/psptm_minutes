"""Аудио → сегменты с таймкодами и спикерами.

Бэкенды распознавания:
  - gigaam  — GigaAM-v3 от Сбера (MIT), лучший WER на русском, e2e-варианты сразу дают пунктуацию.
  - whisper — faster-whisper (large-v3 / large-v3-turbo), если нужна мультиязычность или пословные таймкоды.
Диаризация: pyannote/speaker-diarization-community-1 (CC-BY-4.0), режим exclusive — одна метка на момент
времени, удобно сопоставлять с ASR.

Тяжёлые зависимости импортируются лениво: модуль можно не ставить, если транскрипт уже есть.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def extract_audio(src: str | Path, dst: Path) -> Path:
    """Любое видео/аудио из Телемоста (.webm, .mp4, .m4a) → 16 кГц моно WAV."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)],
        check=True,
    )
    return dst


# ---------- распознавание ----------

def transcribe_gigaam(wav: Path, revision: str = "e2e_rnnt", device: str | None = None) -> list[dict]:
    """GigaAM-v3 через transformers. Длинные записи режутся встроенным transcribe_longform
    (внутри использует VAD из pyannote, нужен HF_TOKEN с принятыми условиями модели).
    Проверено по карточке модели ai-sage/GigaAM-v3; если API поменялся — см. README репозитория GigaAM."""
    from transformers import AutoModel  # type: ignore

    model = AutoModel.from_pretrained("ai-sage/GigaAM-v3", revision=revision, trust_remote_code=True)
    if device:
        model = model.to(device)
    utterances = model.transcribe_longform(str(wav))
    segments = []
    for u in utterances:
        text = u.get("transcription") or u.get("text") or ""
        start, end = u.get("boundaries") or (u.get("start"), u.get("end"))
        segments.append({"start": float(start), "end": float(end), "text": text.strip()})
    return segments


def transcribe_whisper(wav: Path, model_size: str = "large-v3", device: str = "auto", language: str = "ru",
                       compute_type: str = "auto", initial_prompt: str | None = None) -> list[dict]:
    """faster-whisper с VAD и пословными таймкодами (слова нужны для точной привязки спикеров)."""
    from faster_whisper import WhisperModel  # type: ignore

    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    segs, _ = model.transcribe(
        str(wav),
        language=language,
        vad_filter=True,
        word_timestamps=True,
        condition_on_previous_text=False,  # меньше «зацикливаний» на длинных записях
        initial_prompt=initial_prompt,     # сюда удобно передать глоссарий имён и терминов
    )
    out = []
    for s in segs:
        out.append({
            "start": s.start, "end": s.end, "text": s.text.strip(),
            "words": [{"start": w.start, "end": w.end, "word": w.word} for w in (s.words or [])],
        })
    return out


# ---------- диаризация ----------

def diarize(wav: Path, model: str = "pyannote/speaker-diarization-community-1", num_speakers: int | None = None,
            min_speakers: int | None = None, max_speakers: int | None = None, device: str | None = None) -> list[dict]:
    from pyannote.audio import Pipeline  # type: ignore

    token = os.environ.get("HF_TOKEN")
    pipe = Pipeline.from_pretrained(model, token=token) if token else Pipeline.from_pretrained(model)
    if device:
        import torch  # type: ignore
        pipe.to(torch.device(device))
    kwargs = {k: v for k, v in dict(num_speakers=num_speakers, min_speakers=min_speakers, max_speakers=max_speakers).items() if v}
    output = pipe(str(wav), **kwargs)

    # pyannote 4.x: output.exclusive_speaker_diarization; 3.x: сам output — Annotation
    ann = getattr(output, "exclusive_speaker_diarization", None) or getattr(output, "speaker_diarization", None) or output
    turns = []
    if hasattr(ann, "itertracks"):
        for turn, _, spk in ann.itertracks(yield_label=True):
            turns.append({"start": turn.start, "end": turn.end, "speaker": spk})
    else:
        for turn, spk in ann:
            turns.append({"start": turn.start, "end": turn.end, "speaker": spk})
    return _normalize_labels(turns)


def _normalize_labels(turns: list[dict]) -> list[dict]:
    """Нумеруем спикеров в порядке первого появления: SPEAKER_00 — кто заговорил первым."""
    mapping: dict[str, str] = {}
    for t in sorted(turns, key=lambda x: x["start"]):
        if t["speaker"] not in mapping:
            mapping[t["speaker"]] = f"SPEAKER_{len(mapping):02d}"
        t["speaker"] = mapping[t["speaker"]]
    return sorted(turns, key=lambda x: x["start"])


# ---------- сопоставление ASR и диаризации ----------

def _speaker_at(t0: float, t1: float, turns: list[dict]) -> str:
    best, best_ov = None, 0.0
    for tr in turns:
        if tr["end"] < t0:
            continue
        if tr["start"] > t1:
            break
        ov = min(t1, tr["end"]) - max(t0, tr["start"])
        if ov > best_ov:
            best, best_ov = tr["speaker"], ov
    if best is None:  # сегмент попал в паузу — берём ближайший ход
        mid = (t0 + t1) / 2
        best = min(turns, key=lambda tr: min(abs(tr["start"] - mid), abs(tr["end"] - mid)))["speaker"] if turns else "SPEAKER_00"
    return best


def assign_speakers(segments: list[dict], turns: list[dict]) -> list[dict]:
    """Если есть пословные таймкоды — режем сегменты по смене спикера на уровне слов.
    Иначе сегмент целиком отдаём спикеру с максимальным перекрытием."""
    if not turns:
        return [{**s, "speaker": "SPEAKER_00"} for s in segments]
    out: list[dict] = []
    for s in segments:
        words = s.get("words") or []
        if not words:
            out.append({"start": s["start"], "end": s["end"], "speaker": _speaker_at(s["start"], s["end"], turns), "text": s["text"]})
            continue
        cur = None
        for w in words:
            spk = _speaker_at(w["start"], w["end"], turns)
            if cur and cur["speaker"] == spk:
                cur["text"] += w["word"]
                cur["end"] = w["end"]
            else:
                if cur:
                    out.append(cur)
                cur = {"start": w["start"], "end": w["end"], "speaker": spk, "text": w["word"]}
        if cur:
            out.append(cur)
    for o in out:
        o["text"] = o["text"].strip()
    return [o for o in out if o["text"]]


def run_asr(src: Path, out_dir: Path, cfg: dict) -> list[dict]:
    wav = extract_audio(src, out_dir / "audio.wav")
    asr = cfg.get("asr", {})
    if asr.get("backend", "gigaam") == "gigaam":
        segments = transcribe_gigaam(wav, asr.get("gigaam_revision", "e2e_rnnt"), asr.get("device"))
    else:
        segments = transcribe_whisper(wav, asr.get("whisper_model", "large-v3"), asr.get("device") or "auto",
                                      asr.get("language", "ru"), initial_prompt=asr.get("initial_prompt"))
    dz = cfg.get("diarization", {})
    turns = diarize(wav, dz.get("model", "pyannote/speaker-diarization-community-1"), dz.get("num_speakers"),
                    dz.get("min_speakers"), dz.get("max_speakers"), asr.get("device")) if dz.get("enabled", True) else []
    return assign_speakers(segments, turns)

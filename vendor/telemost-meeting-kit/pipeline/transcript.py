"""Транскрипт: разбор форматов, нормализация реплик, нарезка на чанки, сверка цитат.

Не зависит от LLM и тяжёлых библиотек — только стандартная библиотека.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from pathlib import Path


@dataclass
class Line:
    id: str
    start: float | None  # секунды от начала записи
    end: float | None
    speaker: str
    text: str


# ---------- таймкоды ----------

def fmt_ts(sec: float | None) -> str:
    if sec is None:
        return "--:--:--"
    sec = int(round(sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def parse_ts(s: str | None) -> float | None:
    if not s:
        return None
    s = s.strip().replace(",", ".")
    parts = s.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    return nums[0] if nums else None


# ---------- разбор форматов ----------

_TS = r"(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?)"
_TXT_PATTERNS = [
    # [00:01:02] Иван: текст   |   00:01:02 Иван: текст
    re.compile(rf"^\s*\[?{_TS}\]?\s*[-–—]?\s*([^:\[\]]{{1,60}}?)\s*:\s*(.+)$"),
    # Иван [00:01:02]: текст   |   Иван (00:01): текст
    re.compile(rf"^\s*([^:\[\]()]{{1,60}}?)\s*[\[(]{_TS}[\])]\s*:?\s*(.+)$"),
]
_SPEAKER_ONLY = re.compile(r"^\s*([^:\[\]]{1,60}?)\s*:\s*(.+)$")


def _parse_txt(text: str) -> list[dict]:
    rows = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        m = _TXT_PATTERNS[0].match(raw)
        if m:
            rows.append({"start": parse_ts(m.group(1)), "speaker": m.group(2).strip(), "text": m.group(3).strip()})
            continue
        m = _TXT_PATTERNS[1].match(raw)
        if m:
            rows.append({"start": parse_ts(m.group(2)), "speaker": m.group(1).strip(), "text": m.group(3).strip()})
            continue
        m = _SPEAKER_ONLY.match(raw)
        if m and len(m.group(1).split()) <= 4:
            rows.append({"start": None, "speaker": m.group(1).strip(), "text": m.group(2).strip()})
            continue
        if rows:  # продолжение предыдущей реплики
            rows[-1]["text"] += " " + raw.strip()
        else:
            rows.append({"start": None, "speaker": "SPEAKER_00", "text": raw.strip()})
    return rows


def _parse_subtitles(text: str) -> list[dict]:
    """SRT / VTT. Спикер берётся из «Имя: текст» или <v Имя> (VTT)."""
    rows = []
    blocks = re.split(r"\n\s*\n", text.replace("\r", ""))
    for b in blocks:
        m = re.search(rf"{_TS}\s*-->\s*{_TS}", b)
        if not m:
            continue
        body = b[m.end():].strip()
        if not body:
            continue
        body = " ".join(body.splitlines())
        speaker = "SPEAKER_00"
        v = re.match(r"<v\s+([^>]+)>(.*)", body)
        if v:
            speaker, body = v.group(1).strip(), re.sub(r"</v>", "", v.group(2)).strip()
        else:
            s = _SPEAKER_ONLY.match(body)
            if s and len(s.group(1).split()) <= 4:
                speaker, body = s.group(1).strip(), s.group(2).strip()
        body = re.sub(r"<[^>]+>", "", body)
        rows.append({"start": parse_ts(m.group(1)), "end": parse_ts(m.group(2)), "speaker": speaker, "text": body})
    return rows


def load_transcript(path: str | Path) -> list[Line]:
    """Загружает транскрипт из .json (вывод asr.py), .srt, .vtt или .txt/.md."""
    path = Path(path)
    raw = path.read_text(encoding="utf-8-sig")
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(raw)
        rows = data["segments"] if isinstance(data, dict) else data
    elif suffix in (".srt", ".vtt"):
        rows = _parse_subtitles(raw)
    else:
        rows = _parse_txt(raw)
    return to_lines(rows)


def to_lines(rows: list[dict]) -> list[Line]:
    lines = []
    for i, r in enumerate(rows, 1):
        text = (r.get("text") or "").strip()
        if not text:
            continue
        lines.append(Line(
            id=f"L{i:04d}",
            start=r.get("start"),
            end=r.get("end"),
            speaker=(r.get("speaker") or "SPEAKER_00").strip(),
            text=text,
        ))
    return renumber(lines)


def renumber(lines: list[Line]) -> list[Line]:
    for i, ln in enumerate(lines, 1):
        ln.id = f"L{i:04d}"
    return lines


def merge_consecutive(lines: list[Line], max_gap: float = 2.0, max_chars: int = 900) -> list[Line]:
    """Склеивает подряд идущие короткие сегменты одного спикера в реплику."""
    out: list[Line] = []
    last_start: float | None = None  # начало последнего склеенного сегмента (если нет end)
    for ln in lines:
        prev = out[-1] if out else None
        ref = prev.end if prev and prev.end is not None else last_start
        # Без end (txt-транскрипт) допускаем склейку, только если реплики идут почти подряд
        gap_ok = ln.start is None or ref is None or ln.start - ref <= (max_gap if prev and prev.end is not None else 20)
        if prev and prev.speaker == ln.speaker and len(prev.text) + len(ln.text) < max_chars and gap_ok:
            prev.text = f"{prev.text} {ln.text}"
            prev.end = ln.end if ln.end is not None else prev.end
        else:
            out.append(Line(**asdict(ln)))
        last_start = ln.start
    return renumber(out)


def render(lines: list[Line], with_ids: bool = True) -> str:
    out = []
    for ln in lines:
        prefix = f"{ln.id} " if with_ids else ""
        out.append(f"{prefix}[{fmt_ts(ln.start)}] {ln.speaker}: {ln.text}")
    return "\n".join(out)


def save_transcript(lines: list[Line], out_dir: Path) -> None:
    (out_dir / "transcript.md").write_text(render(lines, with_ids=False) + "\n", encoding="utf-8")
    (out_dir / "transcript.json").write_text(
        json.dumps({"segments": [asdict(l) for l in lines]}, ensure_ascii=False, indent=1), encoding="utf-8"
    )


# ---------- токены и чанки ----------

def estimate_tokens(text: str) -> int:
    """Грубая оценка: для русского текста у Qwen/GigaChat/Llama-токенизаторов ~2.8–3.5 символа на токен.
    Берём 3.0 — с запасом."""
    return int(len(text) / 3.0) + 1


def chunk_lines(lines: list[Line], max_tokens: int, overlap_lines: int = 4) -> list[list[Line]]:
    """Режет по границам реплик. Перекрытие в несколько реплик, чтобы не терять контекст на стыке."""
    chunks: list[list[Line]] = []
    cur: list[Line] = []
    cur_tok = 0
    for ln in lines:
        t = estimate_tokens(ln.text) + 12
        if cur and cur_tok + t > max_tokens:
            chunks.append(cur)
            cur = cur[-overlap_lines:] if overlap_lines else []
            cur_tok = sum(estimate_tokens(x.text) + 12 for x in cur)
        cur.append(ln)
        cur_tok += t
    if cur:
        chunks.append(cur)
    return chunks


def window(lines: list[Line], ts: float | None, before: float = 120, after: float = 120) -> list[Line]:
    if ts is None:
        return lines
    return [l for l in lines if l.start is not None and ts - before <= l.start <= ts + after] or lines


# ---------- сверка цитат (антигаллюцинации) ----------

def _norm(s: str) -> list[str]:
    s = unicodedata.normalize("NFKC", s.lower()).replace("ё", "е")
    s = re.sub(r"[^\w\s%]", " ", s)
    return s.split()


def quote_score(quote: str, lines: list[Line]) -> tuple[float, str | None]:
    """Насколько дословно цитата встречается в репликах (0..1) и в какой строке.

    Скользящее окно по словам + SequenceMatcher: устойчиво к мелким расхождениям
    пунктуации и окончаний, но ловит пересказ вместо цитаты."""
    q = _norm(quote or "")
    if len(q) < 2:
        return 0.0, None
    best, best_id = 0.0, None
    # Склеиваем соседние строки парами: цитата может пересекать границу реплик
    texts = [(l.id, _norm(l.text)) for l in lines]
    joined = [(texts[i][0], texts[i][1] + (texts[i + 1][1] if i + 1 < len(texts) else [])) for i in range(len(texts))]
    n = len(q)
    qs = " ".join(q)
    for lid, words in joined:
        if not words:
            continue
        for i in range(0, max(1, len(words) - n + 1)):
            cand = " ".join(words[i:i + n])
            r = SequenceMatcher(None, qs, cand).ratio()
            if r > best:
                best, best_id = r, lid
                if best > 0.97:
                    return best, best_id
    return best, best_id

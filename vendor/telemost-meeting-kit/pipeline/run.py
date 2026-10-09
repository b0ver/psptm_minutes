"""Разбор записи встречи: запись Телемоста (или готовый транскрипт) → транскрипт + набор отчётов.

Примеры:
  python pipeline/run.py --input "Запись встречи 08.10.2026.webm" --title "Синк по запуску" \
      --date 2026-10-08 --participants "Евгений, Марина Соколова, Иван Петров"
  python pipeline/run.py --input transcript.txt --templates protocol,action_items
  python pipeline/run.py --out out/sync --only-render --templates followup_email   # новый шаблон без повторного разбора
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import transcript as T  # noqa: E402
from llm import LLM  # noqa: E402

AUDIO_EXT = {".webm", ".mp4", ".mkv", ".mov", ".m4a", ".mp3", ".wav", ".ogg", ".opus", ".flac"}
LIST_KEYS = ["topics", "decisions", "action_items", "open_questions", "risks", "numbers", "quotes", "ideas", "disagreements", "mentions"]


# ---------- загрузка промптов, схем, шаблонов ----------

def load_prompt(name: str) -> tuple[str, str]:
    text = (ROOT / "prompts" / name).read_text(encoding="utf-8")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    system, user = text.split("===USER===")
    return system.replace("===SYSTEM===", "").strip(), user.strip()


def fill(tpl: str, **kw) -> str:
    for k, v in kw.items():
        tpl = tpl.replace("{{" + k + "}}", str(v))
    return tpl


def load_schema(name: str) -> dict:
    return json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8"))


def load_templates() -> dict[str, dict]:
    return {p.stem: yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "templates").glob("*.yaml"))}


def describe_sections(tpl: dict) -> str:
    out = []
    for i, s in enumerate(tpl["sections"], 1):
        fmt = s.get("format", "list")
        if fmt == "table" and s.get("columns"):
            fmt += " (колонки: " + " | ".join(s["columns"]) + ")"
        out.append(f"{i}. «{s['title']}» — формат: {fmt}. {s['instruction'].strip()}")
    return "\n".join(out)


# ---------- шаги ----------

class Pipeline:
    def __init__(self, cfg: dict, args, out: Path):
        self.cfg, self.args, self.out = cfg, args, out
        self.llm = LLM(cfg["llm"])
        self.meta = {
            "meeting_title": args.title or "Встреча",
            "meeting_date": args.date or dt.date.today().isoformat(),
            "participants": args.participants or "не указаны",
            "glossary": self._glossary(),
        }

    def _glossary(self) -> str:
        if self.args.glossary and Path(self.args.glossary).exists():
            words = [w.strip() for w in Path(self.args.glossary).read_text(encoding="utf-8").splitlines() if w.strip()]
            return ", ".join(words)
        return "нет"

    def system(self, prompt_system: str) -> str:
        base_rules = (ROOT / "prompts" / "00_base_rules.md").read_text(encoding="utf-8")
        base_rules = re.sub(r"<!--.*?-->", "", base_rules, flags=re.S).strip()
        return fill(fill(prompt_system, base_rules=base_rules), **self.meta)

    def log(self, msg: str) -> None:
        print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)

    # 0. вход → реплики
    def load_lines(self) -> list[T.Line]:
        src = Path(self.args.input)
        if src.suffix.lower() in AUDIO_EXT:
            from asr import run_asr
            self.log("Распознаю речь и спикеров (это самый долгий шаг)…")
            rows = run_asr(src, self.out, self.cfg)
            lines = T.to_lines(rows)
        else:
            lines = T.load_transcript(src)
        lines = T.merge_consecutive(lines, max_chars=self.cfg["chunking"].get("max_line_chars", 900))
        self.log(f"Реплик: {len(lines)}, ~{T.estimate_tokens(T.render(lines))} токенов")
        return lines

    # 1. правка ASR
    def cleanup(self, lines: list[T.Line]) -> list[T.Line]:
        sys_p, usr_p = load_prompt("01_asr_cleanup.md")
        schema = load_schema("asr_fixes")
        by_id = {l.id: l for l in lines}
        chunks = T.chunk_lines(lines, self.cfg["chunking"]["cleanup_chunk_tokens"], 0)
        n = 0
        for i, ch in enumerate(chunks, 1):
            self.log(f"Правка распознавания {i}/{len(chunks)}")
            res = self.llm.chat_json(self.system(sys_p), fill(usr_p, chunk=T.render(ch)), schema, "asr_fixes", "cleanup")
            for f in res.get("fixes", []):
                if f.get("line_id") in by_id and f.get("text"):
                    by_id[f["line_id"]].text = f["text"].strip()
                    n += 1
        self.log(f"Исправлено строк: {n}")
        return lines

    # 2. имена спикеров
    def identify_speakers(self, lines: list[T.Line]) -> dict:
        labels = sorted({l.speaker for l in lines if re.fullmatch(r"SPEAKER_\d+", l.speaker)})
        if not labels or self.meta["participants"] == "не указаны":
            return {}
        sys_p, usr_p = load_prompt("02_speaker_identification.md")
        budget = self.cfg["chunking"]["speaker_id_tokens"]
        head = T.chunk_lines(lines, budget // 2, 0)[0]
        extra = []
        for lab in labels:  # самые длинные реплики каждого спикера — там чаще самопредставления и роли
            own = sorted((l for l in lines if l.speaker == lab and l not in head), key=lambda l: -len(l.text))[:4]
            extra.extend(own)
        sample = head + sorted(extra, key=lambda l: l.start or 0)
        res = self.llm.chat_json(self.system(sys_p), fill(usr_p, speaker_labels=", ".join(labels), chunk=T.render(sample)),
                                 load_schema("speakers"), "speakers", "speakers")
        mapping = {}
        for s in res.get("speakers", []):
            if s.get("label") in labels and s.get("name"):
                mapping[s["label"]] = s["name"] if s.get("confidence") in ("high", "medium") else f"{s['label']} (возможно, {s['name']})"
        for l in lines:
            l.speaker = mapping.get(l.speaker, l.speaker)
        (self.out / "speakers.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        self.log(f"Спикеры: {mapping or 'не опознаны'}")
        return mapping

    # 3. MAP: факты по чанкам
    def extract(self, lines: list[T.Line]) -> list[dict]:
        sys_p, usr_p = load_prompt("03_extract_facts.md")
        schema = load_schema("facts")
        chunks = T.chunk_lines(lines, self.cfg["chunking"]["chunk_tokens"], self.cfg["chunking"]["overlap_lines"])
        results, prev = [], "Это начало встречи."
        cache = self.out / "chunks"
        cache.mkdir(exist_ok=True)
        for i, ch in enumerate(chunks, 1):
            f = cache / f"facts_{i:03d}.json"
            if f.exists() and not self.args.force:
                res = json.loads(f.read_text(encoding="utf-8"))
            else:
                self.log(f"Извлекаю факты: фрагмент {i}/{len(chunks)} [{T.fmt_ts(ch[0].start)}–{T.fmt_ts(ch[-1].start)}]")
                res = self.llm.chat_json(
                    self.system(fill(sys_p, chunk_index=i, chunk_total=len(chunks))),
                    fill(usr_p, chunk_index=i, chunk_total=len(chunks), previous_context=prev, chunk=T.render(ch)),
                    schema, "meeting_facts", f"extract_{i}")
                f.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
            results.append(res)
            prev = res.get("summary") or prev
        return results

    # 4. REDUCE: склейка
    def merge(self, parts: list[dict]) -> dict:
        combined = {k: [] for k in LIST_KEYS}
        for p in parts:
            for k in LIST_KEYS:
                combined[k].extend(p.get(k) or [])
        summaries = "\n".join(f"{i}. {p.get('summary', '')}" for i, p in enumerate(parts, 1))
        sys_p, usr_p = load_prompt("04_merge_facts.md")
        self.log("Склеиваю итоги фрагментов")
        merged = self.llm.chat_json(self.system(sys_p), fill(usr_p, chunk_summaries=summaries, facts_json=json.dumps(combined, ensure_ascii=False)),
                                    load_schema("merged_facts"), "merged_facts", "merge")
        # Страховка от «потерянных» пунктов: если модель вернула заметно меньше задач, чем было, — логируем
        if len(merged.get("action_items", [])) < 0.5 * len(combined["action_items"]):
            self.log("⚠ После склейки задач стало вдвое меньше — проверьте chunks/*.json")
        return merged

    # 4.5 проверка задач и решений
    def verify(self, facts: dict, lines: list[T.Line]) -> dict:
        sys_p, usr_p = load_prompt("20_verify_items.md")
        schema = load_schema("verification")
        threshold = self.cfg["verify"]["quote_match_threshold"]
        rejected = []
        for key in ("action_items", "decisions"):
            kept = []
            for item in facts.get(key, []):
                ts = T.parse_ts(item.get("timestamp"))
                win = T.window(lines, ts, 150, 150)
                score, line_id = T.quote_score(item.get("evidence", ""), win)
                item["evidence_match"] = round(score, 2)
                if score >= threshold and item.get("confidence") != "low":
                    item["verified"] = True
                    kept.append(item)
                    continue
                if not self.cfg["verify"].get("llm_check", True):
                    item["verified"] = False
                    kept.append(item)
                    continue
                self.log(f"Проверяю спорный пункт: {(item.get('task') or item.get('text', ''))[:60]}")
                v = self.llm.chat_json(self.system(sys_p), fill(usr_p, item_json=json.dumps(item, ensure_ascii=False), window=T.render(T.window(lines, ts, 90, 90))),
                                       schema, "verification", "verify")
                if v.get("verdict") == "подтверждено":
                    item["verified"] = True
                    kept.append(item)
                elif v.get("verdict") == "исправить" and isinstance(v.get("corrected"), dict):
                    item.update({k: val for k, val in v["corrected"].items() if k in item})
                    item["verified"], item["note"] = True, f"исправлено при проверке: {v.get('reason', '')}"
                    kept.append(item)
                else:
                    rejected.append({"kind": key, "item": item, "reason": v.get("reason")})
            facts[key] = kept
        facts["rejected_items"] = rejected
        self.log(f"Отклонено при проверке: {len(rejected)}")
        return facts

    # 5. выбор шаблонов
    def choose_templates(self, facts: dict, templates: dict) -> list[str]:
        if self.args.templates and self.args.templates != "auto":
            return [t.strip() for t in self.args.templates.split(",")]
        always = self.cfg["templates"]["always"]
        if not self.cfg["templates"].get("auto_classify", True):
            return always + self.cfg["templates"]["default_extra"]
        sys_p, usr_p = load_prompt("05_classify_meeting.md")
        catalog = "\n".join(f"- {k} — {v['description']} Когда: {v.get('when_to_use', '')}" for k, v in templates.items())
        topics = "\n".join(f"- {t.get('title')}: {t.get('summary')}" for t in facts.get("topics", []))
        res = self.llm.chat_json(self.system(fill(sys_p, templates_catalog=catalog)),
                                 fill(usr_p, meeting_summary=facts.get("meeting_summary", ""), topics_list=topics),
                                 load_schema("meeting_type"), "meeting_type", "classify")
        chosen = [t for t in res.get("templates", []) if t in templates]
        self.log(f"Тип встречи: {res.get('meeting_type')} → шаблоны {chosen}")
        return list(dict.fromkeys(always + chosen))

    # 6. отчёты
    def render(self, facts: dict, lines: list[T.Line], names: list[str], templates: dict) -> None:
        sys_p, usr_p = load_prompt("10_render_report.md")
        rep = self.out / "reports"
        rep.mkdir(exist_ok=True)
        full = T.render(lines, with_ids=False)
        transcript_block = ""
        if T.estimate_tokens(full) <= self.cfg["render"]["with_transcript_max_tokens"]:
            transcript_block = f"Полный транскрипт (для уточнения формулировок; факты выше приоритетнее):\n<transcript>\n{full}\n</transcript>"
        public = {k: v for k, v in facts.items() if k != "rejected_items"}
        for name in names:
            tpl = templates.get(name)
            if not tpl:
                self.log(f"Нет шаблона {name}, пропускаю")
                continue
            self.log(f"Отчёт: {tpl['name']}")
            md = self.llm.chat(
                self.system(fill(sys_p, template_name=tpl["name"], template_description=tpl["description"], template_sections=describe_sections(tpl))),
                fill(usr_p, facts_json=json.dumps(public, ensure_ascii=False, indent=1), transcript_block=transcript_block),
                step=f"render_{name}")
            md = re.sub(r"^```(?:markdown|md)?\s*|\s*```$", "", md.strip())
            (rep / f"{name}.md").write_text(md + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Разбор записи встречи локальной LLM")
    ap.add_argument("--input", help="Видео/аудио Телемоста или готовый транскрипт (.txt/.srt/.vtt/.json)")
    ap.add_argument("--out", help="Папка результатов (по умолчанию out/<имя файла>)")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--title")
    ap.add_argument("--date", help="Дата встречи YYYY-MM-DD — нужна для пересчёта «к пятнице» в даты")
    ap.add_argument("--participants", help="Участники через запятую (из приглашения в Календаре)")
    ap.add_argument("--glossary", help="Файл: по одному имени/термину в строке")
    ap.add_argument("--templates", default="auto", help="auto или список id через запятую")
    ap.add_argument("--only-render", action="store_true", help="Взять готовые facts.json и только построить отчёты")
    ap.add_argument("--force", action="store_true", help="Не использовать кэш фрагментов")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if not args.out and not args.input:
        ap.error("нужен --input или --out")
    out = Path(args.out or ROOT / "out" / Path(args.input).stem)
    out.mkdir(parents=True, exist_ok=True)
    p = Pipeline(cfg, args, out)
    templates = load_templates()

    if args.only_render:
        facts = json.loads((out / "facts.json").read_text(encoding="utf-8"))
        lines = T.load_transcript(out / "transcript.json")
        p.meta.update(facts.get("_meta", {}))
    else:
        lines = p.load_lines()
        steps = cfg["steps"]
        if steps.get("asr_cleanup"):
            lines = p.cleanup(lines)
        if steps.get("speaker_id", True):
            p.identify_speakers(lines)
        T.save_transcript(lines, out)
        facts = p.merge(p.extract(lines))
        if steps.get("verify", True):
            facts = p.verify(facts, lines)
        facts["_meta"] = p.meta
        (out / "facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=1), encoding="utf-8")

    names = p.choose_templates(facts, templates)
    p.render(facts, lines, names, templates)
    (out / "run_log.json").write_text(json.dumps({"templates": names, "llm_calls": p.llm.calls}, ensure_ascii=False, indent=1), encoding="utf-8")
    p.log(f"Готово: {out}")


if __name__ == "__main__":
    main()

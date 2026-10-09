"""Клиент к локальной LLM через OpenAI-совместимый API.

Работает с Ollama (http://localhost:11434/v1), llama.cpp server (http://localhost:8080/v1),
vLLM, LM Studio, SGLang. Структурированный вывод — через response_format json_schema,
если сервер его поддерживает, иначе json_object или просто текст + разбор.
"""
from __future__ import annotations

import json
import re
import time

import requests

_THINK = re.compile(r"<think>.*?</think>", re.S)


class LLM:
    def __init__(self, cfg: dict):
        self.base_url = cfg.get("base_url", "http://localhost:11434/v1").rstrip("/")
        self.model = cfg.get("model", "qwen3:14b")
        self.api_key = cfg.get("api_key", "local")
        self.temperature = cfg.get("temperature", 0.2)
        self.max_tokens = cfg.get("max_tokens", 4096)
        self.timeout = cfg.get("timeout", 900)
        self.structured = cfg.get("structured_output", "json_schema")  # json_schema | json_object | none
        self.user_suffix = cfg.get("user_suffix", "")  # например "/no_think" для Qwen3-моделей
        self.extra = cfg.get("extra_body", {}) or {}
        self.calls: list[dict] = []

    # ----- низкий уровень -----
    def _post(self, messages: list[dict], response_format: dict | None) -> str:
        if self.base_url == "mock":
            return _mock_answer(messages, response_format)
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            **self.extra,
        }
        if response_format:
            body["response_format"] = response_format
        last_err = None
        for attempt in range(3):
            try:
                r = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=body,
                    timeout=self.timeout,
                )
                if r.status_code == 400 and response_format and response_format.get("type") == "json_schema":
                    # Сервер не умеет json_schema — откатываемся на json_object
                    body["response_format"] = {"type": "json_object"}
                    continue
                r.raise_for_status()
                data = r.json()
                return data["choices"][0]["message"]["content"] or ""
            except (requests.RequestException, KeyError, ValueError) as e:
                last_err = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"LLM недоступна: {last_err}")

    def chat(self, system: str, user: str, response_format: dict | None = None, step: str = "") -> str:
        if self.user_suffix:
            user = f"{user}\n\n{self.user_suffix}"
        t0 = time.time()
        out = self._post([{"role": "system", "content": system}, {"role": "user", "content": user}], response_format)
        out = _THINK.sub("", out).strip()
        self.calls.append({"step": step, "seconds": round(time.time() - t0, 1), "in_chars": len(system) + len(user), "out_chars": len(out)})
        return out

    # ----- JSON -----
    def chat_json(self, system: str, user: str, schema: dict, name: str, step: str = "") -> dict:
        rf = None
        if self.structured == "json_schema":
            clean = {k: v for k, v in schema.items() if not k.startswith("$") and k != "title"}
            rf = {"type": "json_schema", "json_schema": {"name": name, "schema": clean, "strict": True}}
        elif self.structured == "json_object":
            rf = {"type": "json_object"}
        raw = self.chat(system, user, rf, step=step)
        try:
            return extract_json(raw)
        except ValueError:
            # Одна попытка починки: просим модель вернуть только JSON
            fix = self.chat(
                "Ты исправляешь невалидный JSON. Верни только валидный JSON без пояснений и без markdown.",
                f"Схема:\n{json.dumps(schema, ensure_ascii=False)}\n\nНевалидный ответ:\n{raw}",
                rf,
                step=step + ":repair",
            )
            return extract_json(fix)


def extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Берём первый сбалансированный {...}
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                esc = (c == "\\") and not esc
                if c == '"' and not esc:
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise ValueError("В ответе модели не найден валидный JSON")


# ---------- mock для проверки пайплайна без модели ----------

def _skeleton(schema: dict):
    t = schema.get("type")
    if isinstance(t, list):
        t = [x for x in t if x != "null"][0] if "null" not in t else "null"
    if "enum" in schema:
        return schema["enum"][0]
    if t == "object":
        return {k: _skeleton(v) for k, v in schema.get("properties", {}).items()}
    if t == "array":
        return []
    if t == "string":
        return "mock"
    return None


def _mock_answer(messages: list[dict], response_format: dict | None) -> str:
    if response_format and response_format.get("type") == "json_schema":
        return json.dumps(_skeleton(response_format["json_schema"]["schema"]), ensure_ascii=False)
    if response_format:
        return "{}"
    return "# Mock-отчёт\n\nЗдесь будет текст от модели."

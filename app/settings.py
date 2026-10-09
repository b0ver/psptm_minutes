import json
import os
from pathlib import Path


def secret(name: str) -> str:
    path = os.environ.get(name + "_FILE")
    if not path:
        raise RuntimeError("Required secret file is missing: " + name)
    value = Path(path).read_text().strip()
    if not value:
        raise RuntimeError("Required secret file is empty: " + name)
    return value


def database_url() -> str:
    from sqlalchemy import URL
    return URL.create("postgresql+psycopg", username="minutes", password=secret("DB_PASSWORD"),
                      host=os.getenv("DB_HOST", "db"), database="minutes")


DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
MODEL_PATH = Path(os.getenv("STT_MODEL_PATH", "/models/whisper"))
OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")
POLICY_PATH = Path(os.getenv("MODEL_POLICY_PATH", "/app/config/models.json"))


def model_policy() -> dict:
    return json.loads(POLICY_PATH.read_text())

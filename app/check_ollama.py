"""Read-only connectivity/model-policy probe; never calls inference endpoints."""
import json
import sys

import httpx

from app.ai import ProcessingError, validate_endpoint, verify_model
from app.settings import OLLAMA_URL, OLLAMA_MODEL, model_policy


def main():
    try:
        validate_endpoint(OLLAMA_URL)
        with httpx.Client(base_url=OLLAMA_URL, timeout=8, trust_env=False, follow_redirects=False) as client:
            response = client.get("/api/version")
            response.raise_for_status()
            version = response.json()["version"]
            verify_model(client, OLLAMA_MODEL, model_policy())
            response = client.get("/api/ps")
            response.raise_for_status()
            loaded = len(response.json().get("models", []))
        print(json.dumps({"status": "ok", "ollama_version": version,
                          "model": OLLAMA_MODEL, "model_policy": "verified_local_digest",
                          "loaded_models": loaded, "inference_called": False}))
        return 0
    except ProcessingError as e:
        code = e.code
    except Exception:
        code = "ollama_read_only_probe_failed"
    print(json.dumps({"status": "failed", "code": code, "inference_called": False}))
    return 1


if __name__ == "__main__":
    sys.exit(main())

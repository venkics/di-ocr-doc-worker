# llm_doc_pipeline/orchestrator/ocr/di_client.py
import os, time, base64, requests
from typing import Dict, Any, Optional

API_VER = "2023-07-31"  # legacy route that works on East US via /formrecognizer

def require_env() -> tuple[str, str]:
    endpoint = (os.getenv("DI_ENDPOINT") or "").rstrip("/")
    key = os.getenv("DI_KEY") or ""
    if not endpoint.startswith("https://") or "cognitiveservices.azure.com" not in endpoint:
        raise SystemExit(f"Bad DI_ENDPOINT: {endpoint!r}")
    if not key:
        raise SystemExit("DI_KEY missing")
    return endpoint, key

def payload_from_source(src: str) -> Dict[str, Any]:
    if src.lower().startswith("http"):
        return {"urlSource": src}
    with open(src, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return {"base64Source": b64}

def analyze_model(endpoint: str, key: str, model_id: str, source: str, timeout_sec: int = 60) -> Dict[str, Any]:
    """Submit + poll the REST analyze endpoint; return the full JSON dict."""
    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/json",
    }
    body = payload_from_source(source)
    submit_url = f"{endpoint}/formrecognizer/documentModels/{model_id}:analyze?api-version={API_VER}"
    r = requests.post(submit_url, headers=headers, json=body, timeout=timeout_sec)
    if r.status_code >= 300:
        raise SystemExit(f"[{model_id}] Submit error {r.status_code}: {r.text}")
    oploc = r.headers.get("Operation-Location")
    if not oploc:
        raise SystemExit(f"[{model_id}] No Operation-Location header. Headers: {dict(r.headers)}")
    poll_headers = {"Ocp-Apim-Subscription-Key": key}
    while True:
        pr = requests.get(oploc, headers=poll_headers, timeout=timeout_sec)
        if pr.status_code >= 300:
            raise SystemExit(f"[{model_id}] Poll error {pr.status_code}: {pr.text}")
        data = pr.json()
        status = data.get("status")
        if status in ("succeeded", "failed", "partiallySucceeded"):
            if status != "succeeded":
                print(f"[{model_id}] Status: {status}")
            return data
        time.sleep(2)

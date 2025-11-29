# di-ocr-doc-worker

Combined repo containing two related projects:
- **di-ocr/**: Document intelligence OCR utilities and entrypoint (`main.py`).
- **doc-worker/**: LLM-powered document processing worker, with an orchestrator pipeline under `llm_doc_pipeline/`.

## Repo layout
- `di-ocr/` — Dockerfile, requirements, and settings for OCR-related processing.
- `doc-worker/` — Dockerfile, requirements, `worker.py`, and the `llm_doc_pipeline/` package (orchestrator, OCR helpers, and prompts).

## Quick start (local, Python)
```bash
python -m venv .venv
.\.venv\Scripts\activate        # Windows PowerShell
pip install -r di-ocr/requirements.txt
pip install -r doc-worker/requirements.txt
```

Run the OCR tool:
```bash
python di-ocr/main.py
```

Run the document worker:
```bash
python doc-worker/worker.py
```

## Docker builds
```bash
docker build -t di-ocr ./di-ocr
docker build -t doc-worker ./doc-worker
```

## Notes
- Update `settings.json` or other config files as needed before running.
- Add any secrets/keys via environment variables or local config files; do not commit secrets. 

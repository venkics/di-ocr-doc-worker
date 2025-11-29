# llm_doc_pipeline/orchestrator/ocr/di_rest_quicktest.py
import os, sys, json
from datetime import datetime
from pathlib import Path

# llm_doc_pipeline/orchestrator/ocr/di_rest_quicktest.py

# --- PYTHONPATH bootstrap (fix ModuleNotFoundError: llm_doc_pipeline) ---
import sys, pathlib
# add the project root (parent of "llm_doc_pipeline") to sys.path
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
# --- end bootstrap ---

from llm_doc_pipeline.orchestrator.ocr.di_client import require_env, analyze_model
# ... rest of your existing code ...


# --- NEW: always load .env from this file's folder ---
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=_env_path)
    # Optional debug (comment out later):
    # print("DEBUG AOAI_ENDPOINT =", os.getenv("AOAI_ENDPOINT"))
    # print("DEBUG DI_ENDPOINT   =", os.getenv("DI_ENDPOINT"))
except Exception as _e:
    # Don't crash if dotenv isn't installed; require_env() will still handle errors.
    print("Warning: python-dotenv not loaded:", _e)

from llm_doc_pipeline.orchestrator.ocr.di_client import require_env, analyze_model
from llm_doc_pipeline.orchestrator.ocr.graph_builder import build_layout_graph
from llm_doc_pipeline.orchestrator.ocr.entities_builder import build_entities
from llm_doc_pipeline.orchestrator.ocr.excel_export import export_layout_to_excel
from llm_doc_pipeline.orchestrator.ocr.checkbox_linker import link_checkboxes_right_labels  # NEW
from llm_doc_pipeline.orchestrator.ocr.graph_to_excel import export_graph_to_excel


def make_output_dir(src: str) -> Path:
    base = Path(src).stem.replace(" ", "_")
    date_str = datetime.now().strftime("%Y-%m-%d")
    root = Path(base) / date_str
    root.mkdir(parents=True, exist_ok=True)
    existing = [d for d in root.iterdir() if d.is_dir() and d.name.isdigit()]
    next_num = max([int(d.name) for d in existing], default=0) + 1
    run_dir = root / f"{next_num:03d}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def main():
    if len(sys.argv) < 2:
        print("Usage: python llm_doc_pipeline/orchestrator/ocr/di_rest_quicktest.py <url_or_local_file>")
        sys.exit(1)

    src = sys.argv[1]
    endpoint, key = require_env()
    outdir = make_output_dir(src)
    base = Path(src).stem.replace(" ", "_")
    print(f"Outputs will be written under: {outdir}")

    # Analyze prebuilt-layout
    print("Analyzing with prebuilt-layout …")
    layout = analyze_model(endpoint, key, "prebuilt-layout", src)
    with open(outdir / f"{base}_layout.json", "w", encoding="utf-8") as f:
        json.dump(layout, f, ensure_ascii=False, indent=2)

    # Analyze prebuilt-document
    document = None
    try:
        print("Analyzing with prebuilt-document …")
        document = analyze_model(endpoint, key, "prebuilt-document", src)
        with open(outdir / f"{base}_document.json", "w", encoding="utf-8") as f:
            json.dump(document, f, ensure_ascii=False, indent=2)
    except SystemExit as e:
        print("prebuilt-document not available → skipping (", e, ")")

    # Build layout_graph.json
    graph = build_layout_graph(layout, document)
    with open(outdir / f"{base}_layout_graph.json", "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)

    # Build entities_extracted.json
    entities = build_entities(document)
    with open(outdir / f"{base}_entities_extracted.json", "w", encoding="utf-8") as f:
        json.dump(entities, f, ensure_ascii=False, indent=2)

    # NEW: Checkboxes → right-hand labels
    cb = link_checkboxes_right_labels(layout, only_selected=True)
    with open(outdir / f"{base}_checkboxes_extracted.json", "w", encoding="utf-8") as f:
        json.dump(cb, f, ensure_ascii=False, indent=2)

    # Excel export (includes SelectionMarks and CheckboxesLabeled sheets)
    export_layout_to_excel(layout, str(outdir / f"{base}_layout_extract.xlsx"))

    # Excel summary with Layout + Entities merged
    export_graph_to_excel(layout, graph, entities, str(outdir / f"{base}_layout_graph.xlsx"))
    print("Wrote layout graph Excel:", outdir / f"{base}_layout_graph.xlsx")

    print("All outputs written to:", outdir)


if __name__ == "__main__":
    main()

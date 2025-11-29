# llm_doc_pipeline/orchestrator/ocr/entities_builder.py
from typing import Dict, Any
from llm_doc_pipeline.orchestrator.ocr.utils import poly_to_bbox

def build_entities(document_json: Dict[str, Any] | None) -> Dict[str, Any]:
    """Returns {"entities": [...]} with key/value text, polygons and bboxes."""
    out = {"entities": []}
    if not document_json:
        return out
    doc_ar = document_json.get("analyzeResult", {})
    kv_pairs = doc_ar.get("keyValuePairs", []) or []  # 2023-07-31 camelCase
    for i, kv in enumerate(kv_pairs):
        key = kv.get("key", {}) or {}
        val = kv.get("value", {}) or {}
        def reg(node):
            regs = node.get("boundingRegions") or []
            if not regs: return None, None
            r = regs[0]; return r.get("pageNumber"), r.get("polygon")
        kpg, kpoly = reg(key); vpg, vpoly = reg(val)
        ent = {
            "id": f"kv-{i}",
            "key_text": key.get("content"),
            "value_text": val.get("content"),
            "key_page": kpg, "key_polygon": kpoly, "key_bbox": poly_to_bbox(kpoly),
            "value_page": vpg, "value_polygon": vpoly, "value_bbox": poly_to_bbox(vpoly),
            "confidence": {"key": key.get("confidence"), "value": val.get("confidence")}
        }
        out["entities"].append(ent)
    return out

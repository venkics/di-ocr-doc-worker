# llm_doc_pipeline/orchestrator/ocr/checkbox_linker.py
from typing import Dict, Any, List, Optional
from llm_doc_pipeline.orchestrator.ocr.utils import poly_to_bbox

def _same_line_band(cb_bbox, w_bbox, line_tol: float) -> bool:
    """True if word's vertical center is within a band around checkbox center."""
    cy = (cb_bbox["ymin"] + cb_bbox["ymax"]) / 2.0
    wy = (w_bbox["ymin"] + w_bbox["ymax"]) / 2.0
    return abs(wy - cy) <= line_tol

def _collect_candidates_right(words, cb, max_right_dx, line_tol):
    """Words to the RIGHT of the checkbox, near the same line."""
    cand = []
    for w in words:
        wb = w["bbox"]
        if wb["xmin"] < cb["xmax"]:           # must start to the right of cb
            continue
        if (wb["xmin"] - cb["xmax"]) > max_right_dx:
            continue
        if not _same_line_band(cb, wb, line_tol):
            continue
        cand.append(w)
    # sort left→right
    cand.sort(key=lambda w: (w["bbox"]["xmin"], w["bbox"]["ymin"]))
    return cand

def _collect_candidates_left(words, cb, max_left_dx, line_tol):
    """Words to the LEFT of the checkbox, near the same line."""
    cand = []
    for w in words:
        wb = w["bbox"]
        if wb["xmax"] > cb["xmin"]:           # must end to the left of cb
            continue
        if (cb["xmin"] - wb["xmax"]) > max_left_dx:
            continue
        if not _same_line_band(cb, wb, line_tol):
            continue
        cand.append(w)
    # sort right→left (we’ll reverse later for natural reading)
    cand.sort(key=lambda w: (-w["bbox"]["xmin"], w["bbox"]["ymin"]))
    return cand

def _grow_label_from_seed(cands: List[Dict[str, Any]], join_gap: float, max_label_width: float, direction: str) -> List[Dict[str, Any]]:
    """
    Take the first candidate as seed and greedily append neighbors while:
      - gaps stay small (<= join_gap)
      - total width stays <= max_label_width
    direction: "right" (grow to the right) or "left" (grow to the left).
    """
    if not cands:
        return []
    label = [cands[0]]
    first = cands[0]
    last  = cands[0]

    if direction == "right":
        for w in cands[1:]:
            gap = w["bbox"]["xmin"] - last["bbox"]["xmax"]
            if gap <= join_gap and (w["bbox"]["xmax"] - first["bbox"]["xmin"]) <= max_label_width:
                label.append(w)
                last = w
            else:
                break
        return label

    # direction == "left"
    for w in cands[1:]:
        gap = first["bbox"]["xmin"] - w["bbox"]["xmax"]
        if gap <= join_gap and (last["bbox"]["xmax"] - w["bbox"]["xmin"]) <= max_label_width:
            label.append(w)
            first = w
        else:
            break
    # label is right→left; reverse to natural reading order
    label = list(reversed(label))
    return label

def link_checkboxes_right_labels(
    layout_json: Dict[str, Any],
    only_selected: bool = True,
    max_right_dx: float = 1.8,   # in: search radius to the RIGHT
    max_left_dx: float = 1.8,    # in: search radius to the LEFT (fallback)
    line_tol: float = 0.18,      # in: vertical tolerance for same-line band
    join_gap: float = 0.22,      # in: max gap allowed when joining words
    max_label_width: float = 4.0 # in: safety cap for a label span
) -> Dict[str, Any]:
    """
    For each checkbox (selectionMark):
      1) try to find label to the RIGHT (closest, same line band)
      2) if none, try to find label to the LEFT
    Returns: {"checkboxes":[{page, state, checkbox_bbox, label_words[], label_text}]}
    Units follow the page unit (inches for your docs).
    """
    result = {"checkboxes": []}
    ar = layout_json.get("analyzeResult", {})
    pages = ar.get("pages", []) or []

    for p in pages:
        pnum = p.get("pageNumber")
        unit = p.get("unit")
        # Collect words with bboxes
        words = []
        for i, w in enumerate(p.get("words", []) or []):
            poly = w.get("polygon")
            if not poly: 
                continue
            bb = poly_to_bbox(poly)
            if not bb: 
                continue
            words.append({"index": i, "text": w.get("content",""), "bbox": bb, "polygon": poly})

        marks = p.get("selectionMarks", []) or []
        for m in marks:
            state = m.get("state")  # "selected" | "unselected"
            if only_selected and state != "selected":
                continue
            poly = m.get("polygon")
            if not poly:
                continue
            cb = poly_to_bbox(poly)
            if not cb:
                continue

            # 1) RIGHT search
            rcands = _collect_candidates_right(words, cb, max_right_dx, line_tol)
            rlabel = _grow_label_from_seed(rcands, join_gap, max_label_width, direction="right")
            if rlabel:
                label_text = " ".join(w["text"] for w in rlabel).strip()
                result["checkboxes"].append({
                    "page": pnum,
                    "unit": unit,
                    "state": state,
                    "checkbox_polygon": poly,
                    "checkbox_bbox": cb,
                    "label_words": [{"text": w["text"], "bbox": w["bbox"], "polygon": w["polygon"]} for w in rlabel],
                    "label_text": label_text,
                    "label_side": "right"
                })
                continue

            # 2) LEFT fallback
            lcands = _collect_candidates_left(words, cb, max_left_dx, line_tol)
            llabel = _grow_label_from_seed(lcands, join_gap, max_label_width, direction="left")
            label_text = " ".join(w["text"] for w in llabel).strip() if llabel else ""
            result["checkboxes"].append({
                "page": pnum,
                "unit": unit,
                "state": state,
                "checkbox_polygon": poly,
                "checkbox_bbox": cb,
                "label_words": [{"text": w["text"], "bbox": w["bbox"], "polygon": w["polygon"]} for w in llabel],
                "label_text": label_text,
                "label_side": "left" if llabel else "none"
            })

    return result

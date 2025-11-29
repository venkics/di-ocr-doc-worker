# llm_doc_pipeline/orchestrator/ocr/graph_to_excel.py — layout graph export with robust side/below logic
from __future__ import annotations
from typing import Dict, Any, List, Tuple, Optional
import pandas as pd

# ----------------- geometry helpers -----------------
def poly_to_bbox(poly: Optional[List[float]]) -> Optional[Dict[str, float]]:
    """Convert Azure polygon [x1,y1,x2,y2,...] -> bbox dict or None."""
    if not poly or len(poly) < 8:
        return None
    xs = [poly[i] for i in range(0, len(poly), 2)]
    ys = [poly[i] for i in range(1, len(poly), 2)]
    return {"xmin": min(xs), "xmax": max(xs), "ymin": min(ys), "ymax": max(ys)}

def _bbox(n: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Return node bbox from pre-computed 'bbox' or compute from 'polygon'."""
    return n.get("bbox") or poly_to_bbox(n.get("polygon"))

def _center(bb: Dict[str, float]) -> Tuple[float, float]:
    return ((bb["xmin"] + bb["xmax"]) / 2.0, (bb["ymin"] + bb["ymax"]) / 2.0)

def _horiz_overlap(a: Dict[str, float], b: Dict[str, float]) -> float:
    """Horizontal overlap ratio normalized by min width."""
    left = max(a["xmin"], b["xmin"])
    right = min(a["xmax"], b["xmax"])
    inter = max(0.0, right - left)
    denom = max(1e-6, min(a["xmax"] - a["xmin"], b["xmax"] - b["xmin"]))
    return inter / denom

def _same_line(a: Dict[str, float], b: Dict[str, float], tol: float = 0.12) -> bool:
    """Are two boxes on the same text line? Uses center-Y tolerance (inches)."""
    ay = (a["ymin"] + a["ymax"]) / 2.0
    by = (b["ymin"] + b["ymax"]) / 2.0
    return abs(ay - by) <= tol

# -------------- checkbox label (same-line phrase) --------------
def _checkbox_side_phrase_and_dx(b: Dict[str, float], words_on_line: List[Dict[str, Any]],
                                 max_dx=2.0, max_gap=0.35) -> Tuple[str, Optional[float]]:
    """
    Build a contiguous phrase on the same line:
      - Prefer RIGHT of the checkbox (gap <= max_dx, small in-line gaps).
      - Else take LEFT contiguous chunk (rightmost).
    Returns (phrase, signed_dx) where dx > 0 means to the RIGHT, dx < 0 means LEFT.
    """
    # Right
    right = [w for w in words_on_line if w["bbox"]["xmin"] >= b["xmax"] and (w["bbox"]["xmin"] - b["xmax"]) <= max_dx]
    right.sort(key=lambda w: w["bbox"]["xmin"])
    phrase, prev_xmax = [], None
    for w in right:
        x0, x1 = w["bbox"]["xmin"], w["bbox"]["xmax"]
        if prev_xmax is not None and (x0 - prev_xmax) > max_gap and phrase:
            break
        phrase.append(w["text"]); prev_xmax = x1
    if phrase:
        dx = right[0]["bbox"]["xmin"] - b["xmax"]
        return " ".join(phrase).strip(), round(dx, 3)

    # Left (rightmost contiguous)
    left = [w for w in words_on_line if w["bbox"]["xmax"] <= b["xmin"] and (b["xmin"] - w["bbox"]["xmax"]) <= max_dx]
    left.sort(key=lambda w: w["bbox"]["xmax"])
    phrase, prev_xmin = [], None
    for w in reversed(left):
        x0, x1 = w["bbox"]["xmin"], w["bbox"]["xmax"]
        if prev_xmin is not None and (prev_xmin - x1) > max_gap and phrase:
            break
        phrase.insert(0, w["text"]); prev_xmin = x0
    if phrase:
        dx = -(b["xmin"] - left[-1]["bbox"]["xmax"])
        return " ".join(phrase).strip(), round(dx, 3)

    return "", None

# ----------------- main export -----------------
def export_graph_to_excel(
    layout_json: Dict[str, Any],
    graph_json: Dict[str, Any],
    entities_json: Dict[str, Any],
    xlsx_path: str,
    grid_bins: int = 15,
    # Below-node alignment knobs:
    below_min_overlap: float = 0.10,   # require >= this X-overlap OR…
    below_max_dx: float = 1.25         # …center Δx within this to accept a candidate
) -> None:
    """
    Writes Excel with sheets:
      - LayoutGraph: one row per node with spatial features.
      - Tables, Cells, KV_Nodes, Edges, EntitiesExtracted, EntitiesMerged (if present).
    """

    nodes: List[Dict[str, Any]] = graph_json.get("nodes", []) or []
    edges: List[Dict[str, Any]] = graph_json.get("edges", []) or []
    nodes_by_id = {n.get("id"): n for n in nodes if n.get("id")}
    outgoing = {}
    for e in edges:
        outgoing.setdefault(e["src"], []).append(e)

    # Index WORD nodes by page for fast neighbor search
    words_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for n in nodes:
        if n.get("type") == "word":
            bb = _bbox(n)
            if not bb:
                continue
            pg = int(n.get("page") or 1)
            cx, cy = _center(bb)
            words_by_page.setdefault(pg, []).append({
                "id": n["id"], "text": n.get("text"), "bbox": bb, "cx": cx, "cy": cy
            })

    # Page bounds for grid binning
    page_bounds = {}
    for pg, ws in words_by_page.items():
        if not ws:
            continue
        xmin = min(w["bbox"]["xmin"] for w in ws)
        xmax = max(w["bbox"]["xmax"] for w in ws)
        ymin = min(w["bbox"]["ymin"] for w in ws)
        ymax = max(w["bbox"]["ymax"] for w in ws)
        page_bounds[pg] = {"xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax}

    # Fallback: choose next-in-line from words on the same line band
    def _next_in_line_fallback(curr_id: str, curr_bb: Dict[str, float], pg: int) -> Tuple[Optional[str], Optional[float]]:
        cands = [w for w in words_by_page.get(pg, []) if w["id"] != curr_id and _same_line(curr_bb, w["bbox"])]
        # Prefer RIGHT neighbor (closest), else LEFT (closest from the left)
        right = [w for w in cands if w["bbox"]["xmin"] >= curr_bb["xmax"]]
        right.sort(key=lambda w: (w["bbox"]["xmin"] - curr_bb["xmax"], abs(w["cy"] - (curr_bb["ymin"]+curr_bb["ymax"])/2)))
        if right:
            dx = right[0]["bbox"]["xmin"] - curr_bb["xmax"]
            return right[0]["id"], round(dx, 3)
        left = [w for w in cands if w["bbox"]["xmax"] <= curr_bb["xmin"]]
        left.sort(key=lambda w: -(w["bbox"]["xmax"]))  # rightmost on the left
        if left:
            dx = -(curr_bb["xmin"] - left[0]["bbox"]["xmax"])
            return left[0]["id"], round(dx, 3)
        return None, None

    rows: List[Dict[str, Any]] = []

    for nid, n in nodes_by_id.items():
        t = n.get("type")
        pg = int(n.get("page") or 1)
        bb = _bbox(n)
        if not bb:
            continue
        cx, cy = _center(bb)

        # Grid bins (1..grid_bins)
        pb = page_bounds.get(pg) or {"xmin": 0, "xmax": 8.5, "ymin": 0, "ymax": 11.0}
        gw = (pb["xmax"] - pb["xmin"]) / grid_bins if (pb["xmax"] > pb["xmin"]) else None
        gh = (pb["ymax"] - pb["ymin"]) / grid_bins if (pb["ymax"] > pb["ymin"]) else None
        grid_slice   = int((cy - pb["ymin"]) // gh + 1) if gh else None
        grid_slice_v = int((cx - pb["xmin"]) // gw + 1) if gw else None
        if grid_slice:   grid_slice   = max(1, min(grid_bins, grid_slice))
        if grid_slice_v: grid_slice_v = max(1, min(grid_bins, grid_slice_v))

        # Common outputs
        side_node: Optional[str] = None
        side_dx: Optional[float] = None
        side_text: Optional[str] = None    # for WORD (from side_node) and CHECKBOX (label)
        below_node: Optional[str] = None
        below_text: Optional[str] = None
        below_dy: Optional[float] = None
        checkbox_state = n.get("state") if t == "checkbox" else None

        if t == "word":
            # 1) Try explicit graph edge next-in-line
            for e in outgoing.get(nid, []):
                if e.get("rel") == "next-in-line":
                    dst = e.get("dst")
                    cand = nodes_by_id.get(dst)
                    if cand and cand.get("type") == "word" and cand.get("id") != nid:
                        side_node = cand["id"]
                        wb = _bbox(cand)
                        if wb:
                            side_dx = round((wb["xmin"] - bb["xmax"]) if _center(wb)[0] >= cx
                                            else -(bb["xmin"] - wb["xmax"]), 3)
                    break

            # 2) Fallback: compute from words on same line
            if not side_node:
                side_node, side_dx = _next_in_line_fallback(nid, bb, pg)

            # NEW: side_text for WORD nodes (from the chosen side_node)
            if side_node and side_node in nodes_by_id:
                side_text = nodes_by_id[side_node].get("text") or ""

            # BELOW: immediate aligned word under this one
            cands = []
            for w in words_by_page.get(pg, []):
                if w["id"] == nid:
                    continue
                if w["cy"] <= bb["ymax"] + 1e-4:
                    continue
                aligned = (_horiz_overlap(bb, w["bbox"]) >= below_min_overlap) or (abs(w["cx"] - cx) <= below_max_dx)
                if not aligned:
                    continue
                dy = w["bbox"]["ymin"] - bb["ymax"]  # vertical gap to top of candidate
                dx = abs(w["cx"] - cx)               # tie-breaker
                cands.append((dy, dx, w))
            if cands:
                cands.sort(key=lambda t_: (t_[0], t_[1]))
                best = cands[0][2]
                below_node = best["id"]
                below_text = best["text"]
                below_dy   = round(cands[0][0], 3)

        elif t == "checkbox":
            # Build "same-line" word list by vertical overlap
            same_line_words = []
            for w in words_by_page.get(pg, []):
                yov = max(0.0, min(bb["ymax"], w["bbox"]["ymax"]) - max(bb["ymin"], w["bbox"]["ymin"]))
                if yov > 0:
                    same_line_words.append({"text": w["text"], "bbox": w["bbox"]})
            same_line_words.sort(key=lambda w: w["bbox"]["xmin"])
            label, sdx = _checkbox_side_phrase_and_dx(bb, same_line_words)
            side_text = label or None
            side_dx = sdx

        rows.append({
            "id": nid,
            "type": t,
            "page": pg,
            "text": n.get("text"),
            "xmin": bb["xmin"], "xmax": bb["xmax"], "ymin": bb["ymin"], "ymax": bb["ymax"],
            "grid_slice": grid_slice,
            "grid_slice_v": grid_slice_v,
            "side_node": side_node,      # WORD: neighbor on same line (never equals id)
            "side_text": side_text,      # WORD: neighbor text; CHECKBOX: label phrase
            "side_dx": side_dx,          # signed: + right, - left
            "below_node": below_node,    # WORD: immediate word below
            "below_text": below_text,
            "below_dy": below_dy,        # vertical gap (inches)
            "checkbox_state": checkbox_state
        })

    # ---------- other sheets ----------
    def _collect(kind: str) -> List[Dict[str, Any]]:
        out = []
        for n in nodes:
            if n.get("type") != kind:
                continue
            bb = _bbox(n) or {"xmin": None, "xmax": None, "ymin": None, "ymax": None}
            row = {"id": n.get("id"), "page": n.get("page")}
            if kind == "cell":
                row.update({"table_id": n.get("table_id"), "row": n.get("row"), "col": n.get("col"), "text": n.get("text")})
            if kind in ("kv-key", "kv-val"):
                row.update({"text": n.get("text")})
            row.update({"xmin": bb["xmin"], "xmax": bb["xmax"], "ymin": bb["ymin"], "ymax": bb["ymax"]})
            out.append(row)
        return out

    df_layout = pd.DataFrame(rows)
    df_tables = pd.DataFrame(_collect("table"))
    df_cells  = pd.DataFrame(_collect("cell"))
    df_kv     = pd.DataFrame([r for k in ("kv-key", "kv-val") for r in _collect(k)])
    df_edges  = pd.DataFrame([{"src": e.get("src"), "dst": e.get("dst"), "rel": e.get("rel")} for e in edges])

    def _ent_rows(ejson: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = []
        for e in (ejson or {}).get("entities", []) or []:
            out.append({
                "key_text": e.get("key_text"),
                "value_text": e.get("value_text"),
                "key_page": e.get("key_page"),
                "value_page": e.get("value_page"),
                "key_conf": (e.get("confidence") or {}).get("key"),
                "val_conf": (e.get("confidence") or {}).get("value"),
                "key_bbox": e.get("key_bbox"),
                "val_bbox": e.get("value_bbox")
            })
        return out

    df_ent = pd.DataFrame(_ent_rows(entities_json or {}))
    df_merge = pd.DataFrame([
        {"canonical_key": r.get("key_text"),
         "value": r.get("value_text"),
         "page": r.get("value_page") or r.get("key_page"),
         "source": "kv"}
        for r in _ent_rows(entities_json or {})
    ])

    with pd.ExcelWriter(xlsx_path, engine="xlsxwriter") as w:
        if not df_layout.empty: df_layout.to_excel(excel_writer=w, sheet_name="LayoutGraph", index=False)
        if not df_tables.empty: df_tables.to_excel(excel_writer=w, sheet_name="Tables", index=False)
        if not df_cells.empty:  df_cells.to_excel(excel_writer=w, sheet_name="Cells", index=False)
        if not df_kv.empty:     df_kv.to_excel(excel_writer=w, sheet_name="KV_Nodes", index=False)
        if not df_edges.empty:  df_edges.to_excel(excel_writer=w, sheet_name="Edges", index=False)
        if not df_ent.empty:    df_ent.to_excel(excel_writer=w, sheet_name="EntitiesExtracted", index=False)
        if not df_merge.empty:  df_merge.to_excel(excel_writer=w, sheet_name="EntitiesMerged", index=False)

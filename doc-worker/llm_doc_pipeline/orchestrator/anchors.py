import json, os
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd

# ---------- helpers ----------
def _union(b1, b2):
    if b1 is None: return b2
    if b2 is None: return b1
    x0 = min(b1[0], b2[0]); y0 = min(b1[1], b2[1]); x1 = max(b1[2], b2[2]); y1 = max(b1[3], b2[3])
    return [x0, y0, x1, y1]

def _poly_to_bbox(poly: List[float]) -> List[float]:
    xs = poly[0::2]; ys = poly[1::2]
    return [min(xs), min(ys), max(xs), max(ys)]

# ---------- index building ----------
def build_layout_index(layout_json_path: Optional[str], graph_xlsx_path: Optional[str]) -> Dict[str, Any]:
    """
    Build an index with BOTH page-level lines and WORD-LEVEL tokens so we can anchor precisely.
    Schema:
      {
        "pages": [
          {
            "lines": [{"text": "...", "bbox": [x0,y0,x1,y1]} ...],
            "words": [{"t":"word_text", "bbox":[...]} ...]   # t is lowercased text
          }, ...
        ]
      }
    Coordinates are in the same units as the source (DI page units or normalized 0..1).
    """
    index = {"pages": []}

    # Prefer JSON if present
    if layout_json_path and os.path.exists(layout_json_path):
        try:
            with open(layout_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            pages = data.get("pages") or data.get("analyzeResult", {}).get("pages") or []
            for p in pages:
                page_entry = {"lines": [], "words": []}
                # lines
                for line in p.get("lines", []):
                    text = line.get("content") or line.get("text") or ""
                    poly = line.get("polygon") or line.get("boundingPolygon")
                    bbox = _poly_to_bbox(poly) if poly else None
                    if text:
                        page_entry["lines"].append({"text": text, "bbox": bbox})
                # words
                for w in p.get("words", []):
                    text = w.get("content") or w.get("text") or ""
                    poly = w.get("polygon") or w.get("boundingPolygon")
                    bbox = _poly_to_bbox(poly) if poly else None
                    if text:
                        page_entry["words"].append({"t": str(text).strip().lower(), "bbox": bbox})
                index["pages"].append(page_entry)
            if index["pages"]:
                return index
        except Exception:
            pass

    # Fallback to Excel graph
    if graph_xlsx_path and os.path.exists(graph_xlsx_path):
        try:
            dfs = pd.read_excel(graph_xlsx_path, sheet_name=None, engine="openpyxl")
        except Exception:
            dfs = {}

        # Try to locate a sheet that has word-level rows
        chosen = None
        for name, df in dfs.items():
            cols = {str(c).strip().lower() for c in df.columns}
            if {"text", "page"}.issubset(cols):
                # if there's a 'type' column, prefer rows where type == 'word' or 'line'
                chosen = (name, df)
                break

        if chosen:
            _, df = chosen
            cols_l = {str(c).strip().lower(): c for c in df.columns}
            col_text = cols_l.get("text")
            col_page = cols_l.get("page") or cols_l.get("page_num") or cols_l.get("page_number")
            col_type = cols_l.get("type")
            col_x0 = cols_l.get("x0") or cols_l.get("xmin") or cols_l.get("left")
            col_y0 = cols_l.get("y0") or cols_l.get("ymin") or cols_l.get("top")
            col_x1 = cols_l.get("x1") or cols_l.get("xmax") or cols_l.get("right")
            col_y1 = cols_l.get("y1") or cols_l.get("ymax") or cols_l.get("bottom")

            if col_page is None:
                # assume single page
                df["_page_tmp"] = 1
                col_page = "_page_tmp"

            # group by page
            for pnum, g in df.groupby(col_page):
                page_entry = {"lines": [], "words": []}
                # If there is a 'type' column, split words/lines
                if col_type:
                    gw = g[g[col_type].astype(str).str.lower().eq("word")]
                    gl = g[g[col_type].astype(str).str.lower().eq("line")]
                else:
                    # heuristics: everything is word-level, we'll treat each row as a word
                    gw = g
                    gl = pd.DataFrame(columns=g.columns)

                # words
                for _, r in gw.iterrows():
                    t = str(r.get(col_text, "")).strip()
                    if not t: 
                        continue
                    bbox = None
                    if all(c is not None for c in [col_x0, col_y0, col_x1, col_y1]):
                        try:
                            bbox = [float(r[col_x0]), float(r[col_y0]), float(r[col_x1]), float(r[col_y1])]
                        except Exception:
                            bbox = None
                    page_entry["words"].append({"t": t.lower(), "bbox": bbox})

                # lines (optional)
                if not gl.empty:
                    for _, r in gl.iterrows():
                        t = str(r.get(col_text, "")).strip()
                        bbox = None
                        if all(c is not None for c in [col_x0, col_y0, col_x1, col_y1]):
                            try:
                                bbox = [float(r[col_x0]), float(r[col_y0]), float(r[col_x1]), float(r[col_y1])]
                            except Exception:
                                bbox = None
                        if t:
                            page_entry["lines"].append({"text": t, "bbox": bbox})

                # As a safety net, if no lines exist, create one synthetic line by unioning all words
                if not page_entry["lines"] and page_entry["words"]:
                    full_text = " ".join(w["t"] for w in page_entry["words"])
                    bbox = None
                    for w in page_entry["words"]:
                        bbox = _union(bbox, w.get("bbox"))
                    page_entry["lines"].append({"text": full_text, "bbox": bbox})

                index["pages"].append(page_entry)

    return index

# ---------- anchoring ----------
def _find_in_words(words: List[Dict[str, Any]], target: str, window: int = 6) -> Optional[List[float]]:
    """
    Sliding window over lowercase word tokens to find a short phrase.
    Returns union bbox of the matched window.
    """
    toks = [w["t"] for w in words]
    n = len(toks)
    target = " ".join(target.lower().split())
    # try exact windowed matches
    for k in range(n):
        acc = []
        bbox = None
        for j in range(window):
            if k + j >= n: break
            acc.append(toks[k + j])
            cand = " ".join(acc)
            if target == cand:
                for u in range(j + 1):
                    bbox = _union(bbox, words[k + u].get("bbox"))
                return bbox
            # small tolerance: target contained within cand
            if target in cand:
                for u in range(j + 1):
                    bbox = _union(bbox, words[k + u].get("bbox"))
                return bbox
    return None

def find_snippet_bbox(index: Dict[str, Any], snippet_or_value: str) -> Tuple[Optional[int], Optional[List[float]]]:
    """
    Try to find a short snippet (or value) by:
      1) line contains
      2) word-level sliding window (precise)
    Returns (page_index_1_based, bbox).
    """
    if not snippet_or_value:
        return None, None
    q = " ".join(str(snippet_or_value).strip().split()).lower()

    for pi, page in enumerate(index.get("pages", []), start=1):
        # 1) lines
        for line in page.get("lines", []):
            text = (line.get("text") or "").strip().lower()
            if q and q in text:
                return pi, line.get("bbox")  # coarse bbox
        # 2) words
        bbox = _find_in_words(page.get("words", []), q)
        if bbox is not None:
            return pi, bbox

    return None, None

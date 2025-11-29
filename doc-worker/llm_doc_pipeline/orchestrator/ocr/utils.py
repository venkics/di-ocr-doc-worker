# llm_doc_pipeline/orchestrator/ocr/utils.py
from typing import List, Dict, Optional

def poly_to_bbox(poly: Optional[List[float]]) -> Optional[Dict[str, float]]:
    if not poly:
        return None
    xs = poly[0::2]; ys = poly[1::2]
    return {"xmin": min(xs), "ymin": min(ys), "xmax": max(xs), "ymax": max(ys)}

def overlaps(b1: Dict[str,float], b2: Dict[str,float], tol: float = 1e-6) -> bool:
    return not (b1["xmax"] < b2["xmin"] - tol or b2["xmax"] < b1["xmin"] - tol or
                b1["ymax"] < b2["ymin"] - tol or b2["ymax"] < b1["ymin"] - tol)

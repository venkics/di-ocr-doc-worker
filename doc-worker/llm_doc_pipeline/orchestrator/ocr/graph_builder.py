# llm_doc_pipeline/orchestrator/ocr/graph_builder.py
from typing import Dict, Any, List
from llm_doc_pipeline.orchestrator.ocr.utils import poly_to_bbox, overlaps

def build_layout_graph(layout_json: Dict[str, Any], document_json: Dict[str, Any] | None) -> Dict[str, Any]:
    """
    Nodes:
      - word         p{page}-w{index}
      - table        p{page}-t{ti}
      - cell         p{page}-t{ti}-r{r}-c{c}
      - checkbox     p{page}-cb{idx}
      - kv-key       kv-{i}-key
      - kv-val       kv-{i}-val
    Edges:
      - next-in-line   (word -> next word on same page)
      - in-table       (cell -> table)
      - in-cell        (word -> cell) by bbox overlap
      - kv-pair        (kv-key -> kv-val)
      - overlaps       (kv-val -> word) by bbox overlap
      - cb-in-cell     (checkbox -> cell) by bbox overlap
      - cb-near-word   (checkbox -> word) by bbox overlap
    """
    graph: Dict[str, Any] = {"nodes": [], "edges": []}

    def add_node(n_type: str, node_id: str, **attrs):
        n = {"id": node_id, "type": n_type}
        n.update(attrs)
        graph["nodes"].append(n)

    def add_edge(src: str, dst: str, rel: str):
        graph["edges"].append({"src": src, "dst": dst, "rel": rel})

    ar = (layout_json or {}).get("analyzeResult", {})
    pages = ar.get("pages", []) or []
    tables = ar.get("tables", []) or []

    page_word_nodes: Dict[int, List[Dict[str, Any]]] = {}
    page_cell_nodes: Dict[int, List[Dict[str, Any]]] = {}
    page_checkbox_nodes: Dict[int, List[Dict[str, Any]]] = {}

    # ---- WORDS (with next-in-line heuristic) ----
    for p in pages:
        pnum = p.get("pageNumber")
        unit = p.get("unit")
        words = p.get("words", []) or []
        word_nodes = []
        for wi, w in enumerate(words):
            wid = f"p{pnum}-w{wi}"
            poly = w.get("polygon")
            add_node(
                "word",
                wid,
                page=pnum,
                unit=unit,
                polygon=poly,
                bbox=poly_to_bbox(poly),
                text=w.get("content", ""),
                confidence=w.get("confidence"),
            )
            word_nodes.append(
                {
                    "id": wid,
                    "bbox": poly_to_bbox(poly),
                    "polygon": poly,
                    "text": w.get("content", ""),
                }
            )
        for i in range(len(word_nodes) - 1):
            add_edge(word_nodes[i]["id"], word_nodes[i + 1]["id"], "next-in-line")
        page_word_nodes[pnum] = word_nodes

    # ---- TABLES & CELLS ----
    for ti, t in enumerate(tables):
        tbr = (t.get("boundingRegions") or [{}])[0]
        tpage = tbr.get("pageNumber")
        tpoly = tbr.get("polygon")
        tid = f"p{tpage}-t{ti}"
        add_node(
            "table",
            tid,
            page=tpage,
            polygon=tpoly,
            bbox=poly_to_bbox(tpoly),
            rowCount=t.get("rowCount"),
            columnCount=t.get("columnCount"),
        )
        for c in t.get("cells", []):
            r, cidx = c.get("rowIndex"), c.get("columnIndex")
            cbr = (c.get("boundingRegions") or [{}])[0]
            cpage = cbr.get("pageNumber", tpage)
            cpoly = cbr.get("polygon")
            cid = f"p{cpage}-t{ti}-r{r}-c{cidx}"
            cb = poly_to_bbox(cpoly)
            add_node(
                "cell",
                cid,
                page=cpage,
                polygon=cpoly,
                bbox=cb,
                row=r,
                col=cidx,
                text=c.get("content", ""),
            )
            add_edge(cid, tid, "in-table")
            page_cell_nodes.setdefault(cpage, []).append({"id": cid, "bbox": cb})

            # link words -> cell by overlap
            if cb:
                for w in page_word_nodes.get(cpage, []):
                    if w["bbox"] and overlaps(w["bbox"], cb):
                        add_edge(w["id"], cid, "in-cell")

    # ---- CHECKBOXES (selectionMarks) ----
    for p in pages:
        pnum = p.get("pageNumber")
        unit = p.get("unit")
        marks = p.get("selectionMarks", []) or []
        cb_nodes = []
        for mi, m in enumerate(marks):
            mid = f"p{pnum}-cb{mi}"
            poly = m.get("polygon")
            state = m.get("state")            # "selected" | "unselected"
            conf = m.get("confidence")
            bb = poly_to_bbox(poly)
            add_node(
                "checkbox",
                mid,
                page=pnum,
                unit=unit,
                state=state,
                confidence=conf,
                polygon=poly,
                bbox=bb,
            )
            cb_nodes.append({"id": mid, "bbox": bb})
        page_checkbox_nodes[pnum] = cb_nodes

    # link checkbox -> cell / word by overlap
    for pnum, cbs in page_checkbox_nodes.items():
        # to cells
        for cb in cbs:
            bb = cb["bbox"]
            if not bb:
                continue
            for cell in page_cell_nodes.get(pnum, []):
                if cell["bbox"] and overlaps(bb, cell["bbox"]):
                    add_edge(cb["id"], cell["id"], "cb-in-cell")
            # to words (optional but helpful for labels)
            for w in page_word_nodes.get(pnum, []):
                if w["bbox"] and overlaps(bb, w["bbox"]):
                    add_edge(cb["id"], w["id"], "cb-near-word")

    # ---- KVs (if document_json present) ----
    if document_json:
        doc_ar = document_json.get("analyzeResult", {})
        kv_pairs = doc_ar.get("keyValuePairs", []) or []  # 2023-07-31 camelCase
        for i, kv in enumerate(kv_pairs):
            key = kv.get("key", {}) or {}
            val = kv.get("value", {}) or {}
            def reg(node):
                regs = node.get("boundingRegions") or []
                if not regs:
                    return None, None
                r = regs[0]
                return r.get("pageNumber"), r.get("polygon")
            kpg, kpoly = reg(key)
            vpg, vpoly = reg(val)
            kid = f"kv-{i}-key"
            vid = f"kv-{i}-val"
            add_node("kv-key", kid, page=kpg, polygon=kpoly, bbox=poly_to_bbox(kpoly), text=key.get("content"))
            add_node("kv-val", vid, page=vpg, polygon=vpoly, bbox=poly_to_bbox(vpoly), text=val.get("content"))
            add_edge(kid, vid, "kv-pair")
            vb = poly_to_bbox(vpoly)
            if vpg and vb:
                for w in page_word_nodes.get(vpg, []):
                    if w["bbox"] and overlaps(w["bbox"], vb):
                        add_edge(vid, w["id"], "overlaps")

    return graph

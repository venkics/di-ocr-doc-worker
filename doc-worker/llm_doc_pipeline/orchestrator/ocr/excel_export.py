# llm_doc_pipeline/orchestrator/ocr/excel_export.py
import pandas as pd
from typing import Dict, Any
from llm_doc_pipeline.orchestrator.ocr.utils import poly_to_bbox
from llm_doc_pipeline.orchestrator.ocr.checkbox_linker import link_checkboxes_right_labels

def export_layout_to_excel(layout_json: Dict[str, Any], xlsx_path: str) -> None:
    ar = layout_json.get("analyzeResult", {})
    pages = ar.get("pages", []) or []
    tables = ar.get("tables", []) or []

    # Pages sheet
    pages_rows = []
    for p in pages:
        pages_rows.append({
            "pageNumber": p.get("pageNumber"),
            "width": p.get("width"),
            "height": p.get("height"),
            "unit": p.get("unit"),
            "numWords": len(p.get("words", [])),
            "numLines": len(p.get("lines", [])) if p.get("lines") else None
        })
    df_pages = pd.DataFrame(pages_rows)

    # Words sheet
    words_rows = []
    for p in pages:
        pnum = p.get("pageNumber")
        for i, w in enumerate(p.get("words", []) or []):
            poly = w.get("polygon")
            bbox = poly_to_bbox(poly) or {}
            words_rows.append({
                "pageNumber": pnum,
                "word_index": i,
                "text": w.get("content"),
                "confidence": w.get("confidence"),
                "xmin": bbox.get("xmin"), "ymin": bbox.get("ymin"),
                "xmax": bbox.get("xmax"), "ymax": bbox.get("ymax"),
                "polygon": poly
            })
    df_words = pd.DataFrame(words_rows)

    # Tables index + per-table sheets
    tbl_index = []
    table_sheets = {}
    for ti, t in enumerate(tables):
        tbr = (t.get("boundingRegions") or [{}])[0]
        tpage = tbr.get("pageNumber")
        rowCount = t.get("rowCount", 0)
        colCount = t.get("columnCount", 0)
        tbl_index.append({"table_id": f"p{tpage}_t{ti}", "pageNumber": tpage, "rows": rowCount, "cols": colCount})

        grid = [["" for _ in range(colCount)] for _ in range(rowCount)]
        details_rows = []
        for c in t.get("cells", []):
            r = c.get("rowIndex"); cidx = c.get("columnIndex")
            txt = c.get("content", "")
            grid[r][cidx] = txt if not grid[r][cidx] else (grid[r][cidx] + " " + txt)
            cbr = (c.get("boundingRegions") or [{}])[0]
            poly = cbr.get("polygon")
            bbox = poly_to_bbox(poly)
            details_rows.append({
                "row": r, "col": cidx, "text": txt,
                "xmin": bbox.get("xmin") if bbox else None,
                "ymin": bbox.get("ymin") if bbox else None,
                "xmax": bbox.get("xmax") if bbox else None,
                "ymax": bbox.get("ymax") if bbox else None,
                "polygon": poly
            })
        table_sheets[f"Table_p{tpage}_t{ti}"] = pd.DataFrame(grid)
        table_sheets[f"Table_p{tpage}_t{ti}_cells"] = pd.DataFrame(details_rows)

    df_tbl_index = pd.DataFrame(tbl_index)

    # SelectionMarks (raw)
    sm_rows = []
    for p in pages:
        pnum = p.get("pageNumber")
        for m in p.get("selectionMarks", []) or []:
            poly = m.get("polygon")
            bbox = poly_to_bbox(poly)
            sm_rows.append({
                "pageNumber": pnum,
                "state": m.get("state"),
                "confidence": m.get("confidence"),
                "xmin": bbox.get("xmin") if bbox else None,
                "ymin": bbox.get("ymin") if bbox else None,
                "xmax": bbox.get("xmax") if bbox else None,
                "ymax": bbox.get("ymax") if bbox else None,
                "polygon": poly
            })
    df_marks = pd.DataFrame(sm_rows)

    # NEW: CheckboxesLabeled (selected → right-hand label)
    cb = link_checkboxes_right_labels(layout_json, only_selected=True)
    labeled_rows = []
    for item in cb.get("checkboxes", []):
        labeled_rows.append({
            "pageNumber": item["page"],
            "state": item["state"],
            "label_text": item.get("label_text",""),
            "cb_xmin": item["checkbox_bbox"]["xmin"],
            "cb_ymin": item["checkbox_bbox"]["ymin"],
            "cb_xmax": item["checkbox_bbox"]["xmax"],
            "cb_ymax": item["checkbox_bbox"]["ymax"],
            "label_words_count": len(item.get("label_words", [])),
            "checkbox_polygon": item.get("checkbox_polygon"),
        })
    df_cb_labeled = pd.DataFrame(labeled_rows)

    # Write Excel
    with pd.ExcelWriter(xlsx_path, engine="xlsxwriter") as writer:
        df_pages.to_excel(writer, sheet_name="Pages", index=False)
        df_words.to_excel(writer, sheet_name="Words", index=False)
        df_tbl_index.to_excel(writer, sheet_name="Tables_Index", index=False)
        for name, df in table_sheets.items():
            safe = name[:31]
            df.to_excel(writer, sheet_name=safe, index=False)
        if not df_marks.empty:
            df_marks.to_excel(writer, sheet_name="SelectionMarks", index=False)
        if not df_cb_labeled.empty:
            df_cb_labeled.to_excel(writer, sheet_name="CheckboxesLabeled", index=False)

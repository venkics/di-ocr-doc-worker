import os, json, tempfile
from typing import List, Tuple, Dict, Optional
import streamlit as st
from PIL import Image, ImageDraw
import fitz  # PyMuPDF
import pandas as pd

from orchestrator.graph import run_pipeline
from orchestrator.schemas import PipelineState

st.set_page_config(page_title="LLM Doc POC (DI + LangGraph)", layout="wide")

from pathlib import Path as _Path
def _resolve_di_script(di_script: str) -> str:
    try:
        p = _Path(di_script).expanduser()
        if p.exists():
            return str(p.resolve())
        root = _Path(__file__).resolve().parent
        cand = (root / _Path(di_script)).resolve()
        if cand.exists():
            return str(cand)
        ocr_dir = root / "orchestrator" / "ocr"
        base = _Path(di_script).name
        cand2 = (ocr_dir / base).resolve()
        if cand2.exists():
            return str(cand2)
    except Exception:
        pass
    return di_script

# --------------------------
# Sidebar config
# --------------------------
st.sidebar.title("Settings")
di_script = st.sidebar.text_input(
    "Path to DI runner (di_rest_quicktest.py)",
    value="/home/azureuser/llm_demo_org/llm_doc_pipeline/orchestrator/ocr/di_rest_quicktest.py",
)
scope_xlsx = st.sidebar.text_input(
    "Path to Scope_Workbook.xlsx",
    value="/home/azureuser/llm_demo_org/Scope_Workbook.xlsx",
)
st.sidebar.caption("Azure OpenAI creds loaded from your .env / environment.")

st.title("Tech Lotus - AI Pipeline")
st.caption("Two-pane view with robust paging + correct bbox scaling.")

uploaded = st.file_uploader(
    "Upload one or more PDFs/TIFFs/Images",
    type=["pdf", "tif", "tiff", "png", "jpg", "jpeg"],
    accept_multiple_files=True,
)

# --------------------------
# Helpers
# --------------------------
def save_uploads(files) -> List[str]:
    paths = []
    if not files:
        return paths
    up_dir = tempfile.mkdtemp(prefix="uploads_")
    for f in files:
        p = os.path.join(up_dir, f.name)
        with open(p, "wb") as w:
            w.write(f.read())
        paths.append(p)
    return paths

def load_layout_page_dims(layout_json_path: Optional[str]) -> Dict[int, Tuple[float, float]]:
    dims: Dict[int, Tuple[float, float]] = {}
    if not layout_json_path or not os.path.exists(layout_json_path):
        return dims
    try:
        with open(layout_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        pages = data.get("pages")
        if pages is None:
            pages = data.get("analyzeResult", {}).get("pages", [])
        for idx, p in enumerate(pages, start=1):
            w = p.get("width") or p.get("pageWidth")
            h = p.get("height") or p.get("pageHeight")
            if w and h:
                try:
                    dims[idx] = (float(w), float(h))
                except Exception:
                    pass
    except Exception:
        pass
    return dims

def scale_bbox_to_image(bbox, page_num, img_size, layout_dims):
    if not bbox or not page_num:
        return None
    x0, y0, x1, y1 = bbox
    W, H = img_size
    # normalized 0..1?
    if max(x0, y0, x1, y1) <= 1.5:
        sx0, sy0 = int(x0 * W), int(y0 * H)
        sx1, sy1 = int(x1 * W), int(y1 * H)
        return [max(0, sx0), max(0, sy0), min(W - 1, sx1), min(H - 1, sy1)]
    # layout dims to pixels
    pw, ph = layout_dims.get(page_num, (None, None))
    if pw and ph and pw > 0 and ph > 0:
        sx0, sy0 = int(x0 * (W / pw)), int(y0 * (H / ph))
        sx1, sy1 = int(x1 * (W / pw)), int(y1 * (H / ph))
        return [max(0, sx0), max(0, sy0), min(W - 1, sx1), min(H - 1, sy1)]
    # assume absolute pixels
    sx0, sy0, sx1, sy1 = int(x0), int(y0), int(x1), int(y1)
    return [max(0, sx0), max(0, sy0), min(W - 1, sx1), min(H - 1, sy1)]

def draw_bbox(img: Image.Image, bbox_px: Optional[List[int]], width: int = 4) -> Image.Image:
    if not bbox_px:
        return img
    x0, y0, x1, y1 = bbox_px
    img2 = img.copy()
    draw = ImageDraw.Draw(img2)
    draw.rectangle([x0, y0, x1, y1], outline="red", width=width)
    return img2

def urgency_badge(urgency: str) -> str:
    u = (urgency or "unknown").lower().strip()
    if u == "emergent":
        emoji, bg = "🔴", "#b91c1c"
    elif u == "urgent":
        emoji, bg = "🟠", "#c2410c"
    elif u == "routine":
        emoji, bg = "🟢", "#15803d"
    else:
        emoji, bg = "⚪", "#374151"
    return f"<span style='display:inline-block;padding:.25rem .6rem;border-radius:999px;background:{bg};color:white;font-weight:600'>{emoji} {u}</span>"

# Safety helpers
def _is_valid_bbox(b):
    return isinstance(b, (list, tuple)) and len(b) == 4 and all(isinstance(x, (int, float)) for x in b)

def _safe_int(x, default=None):
    try:
        import math
        if x is None:
            return default
        if isinstance(x, float) and (math.isnan(x)):
            return default
        return int(x)
    except Exception:
        return default

# --------------------------
# Run pipeline
# --------------------------
if st.button("Process with LangGraph", disabled=not uploaded, type="primary"):
    input_files = save_uploads(uploaded)
    state = run_pipeline(input_files, _resolve_di_script(di_script), scope_xlsx)
    if isinstance(state, dict):
        state = PipelineState(**state)
    st.session_state["pipeline_state"] = state
    for job in state.jobs:
        st.session_state[f"page_{job.input_path}"] = 1
        st.session_state[f"hl_{job.input_path}"] = None

state: Optional[PipelineState] = st.session_state.get("pipeline_state")

# --------------------------
# Per-file UI (two-pane)
# --------------------------
if state:
    for job in state.jobs:
        st.subheader(os.path.basename(job.input_path))

        layout_dims = load_layout_page_dims(job.layout_json)

        cols = st.columns([1.2, 1.0], gap="large")

        # ------------- LEFT PANE
        with cols[0]:
            key_page = f"page_{job.input_path}"
            if key_page not in st.session_state:
                st.session_state[key_page] = 1

            # compute total pages lazily
            total_pages = 1
            try:
                ext = os.path.splitext(job.input_path)[1].lower()
                if ext == ".pdf":
                    with fitz.open(job.input_path) as doc:
                        total_pages = len(doc)
                else:
                    from PIL import Image as _PIL_Image
                    with _PIL_Image.open(job.input_path) as im:
                        total_pages = getattr(im, "n_frames", 1)
            except Exception:
                pass

            st.markdown(f"**Total pages:** {total_pages}")
            nav = st.columns([0.2, 0.6, 0.2])
            with nav[0]:
                if st.button("◀ Prev", key=f"prev_{job.input_path}") and st.session_state[key_page] > 1:
                    st.session_state[key_page] -= 1
            with nav[2]:
                if st.button("Next ▶", key=f"next_{job.input_path}") and st.session_state[key_page] < total_pages:
                    st.session_state[key_page] += 1

            current_page = max(1, min(st.session_state[key_page], total_pages))
            st.session_state[key_page] = current_page
            st.write(f"**Page {current_page} / {total_pages}**")

            # render page
            base_img = None
            try:
                if os.path.splitext(job.input_path)[1].lower() == ".pdf":
                    with fitz.open(job.input_path) as doc:
                        page = doc.load_page(current_page - 1)
                        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
                        base_img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                else:
                    from PIL import Image as _PIL_Image
                    with _PIL_Image.open(job.input_path) as im:
                        frames = getattr(im, "n_frames", 1)
                        if getattr(im, "is_animated", False):
                            im.seek(current_page - 1 if current_page <= frames else 0)
                        base_img = im.convert("RGB")
            except Exception as e:
                st.error(f"Unable to render the page: {e}")

            # highlight overlay
            hl_key = f"hl_{job.input_path}"
            hl = st.session_state.get(hl_key)
            if base_img is not None:
                img_to_show = base_img
                if hl and hl.get("page") == current_page and hl.get("bbox"):
                    bbox_px = scale_bbox_to_image(hl["bbox"], hl["page"], base_img.size, layout_dims)
                    img_to_show = draw_bbox(base_img, bbox_px)
                st.image(img_to_show, caption=f"Page {current_page}", use_column_width=True)

            with st.expander("Show all pages (preview)"):
                for p in range(1, total_pages + 1):
                    try:
                        if os.path.splitext(job.input_path)[1].lower() == ".pdf":
                            with fitz.open(job.input_path) as doc:
                                pg = doc.load_page(p - 1)
                                pix = pg.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                                img_p = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                        else:
                            from PIL import Image as _PIL_Image
                            with _PIL_Image.open(job.input_path) as im:
                                frames = getattr(im, "n_frames", 1)
                                if getattr(im, "is_animated", False):
                                    im.seek(p - 1 if p <= frames else 0)
                                img_p = im.convert("RGB")

                        if hl and hl.get("page") == p and hl.get("bbox"):
                            bbox_px = scale_bbox_to_image(hl["bbox"], p, img_p.size, layout_dims)
                            img_p = draw_bbox(img_p, bbox_px)
                        st.image(img_p, caption=f"Page {p}", use_container_width=True)
                    except Exception as e:
                        st.warning(f"Could not render page {p}: {e}")

        # ------------- RIGHT PANE
        with cols[1]:
            tabs = st.tabs(["Classification", "Summary", "Entities", "Triage", "Artifacts"])

            with tabs[0]:
                st.json(job._classification.model_dump())

            with tabs[1]:
                st.json(job._summary.model_dump())

            with tabs[2]:
                ent = job._entities
                if ent.scalar:
                    df_scalar = pd.DataFrame(
                        [
                            {
                                "Entity": k,
                                "Value": v.value,
                                "Snippet": v.source_snippet,
                                "Page": v.page,
                                "BBox": v.bbox,
                            }
                            for k, v in ent.scalar.items()
                        ]
                    )
                    st.markdown("**Scalars**")
                    st.dataframe(df_scalar, use_container_width=True)
                else:
                    st.info("No scalar entities found.")

                if ent.lists:
                    st.markdown("**Lists**")
                    for lname, lrows in ent.lists.items():
                        st.markdown(f"_{lname}_")
                        rows = []
                        for ridx, row in enumerate(lrows, start=1):
                            for k, v in row.items():
                                rows.append(
                                    {
                                        "Row#": ridx,
                                        "Field": k,
                                        "Value": v.value,
                                        "Snippet": v.source_snippet,
                                        "Page": v.page,
                                        "BBox": v.bbox,
                                    }
                                )
                        st.dataframe(pd.DataFrame(rows), use_container_width=True)

                st.markdown("---")
                scalar_names = list(ent.scalar.keys())
                sel = st.selectbox(
                    "Select an entity to highlight (left pane)",
                    scalar_names,
                    index=0 if scalar_names else None,
                    placeholder="Select…",
                    key=f"sel_{job.input_path}",
                )

                if st.button("Highlight", disabled=not scalar_names, key=f"do_hl_{job.input_path}"):
                    tgt = ent.scalar.get(sel)
                    if tgt and tgt.page and tgt.bbox:
                        st.session_state[f"page_{job.input_path}"] = _safe_int(tgt.page, 1)
                        st.session_state[f"hl_{job.input_path}"] = {"page": _safe_int(tgt.page, 1), "bbox": tgt.bbox}
                        st.rerun()
                    else:
                        st.info("That entity does not have page/bbox info to highlight.")

            with tabs[3]:
                tri = getattr(job, "_triage", None)
                if not tri or not tri.get("applicable", False):
                    st.info("No triage information applicable for this document.")
                else:
                    # Color badge + metrics
                    st.markdown(urgency_badge(tri.get("urgency")), unsafe_allow_html=True)
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        st.metric("Urgency", tri.get("urgency", "unknown"))
                    with c2:
                        st.metric("Disposition", tri.get("disposition", "unknown"))
                    with c3:
                        conf = float(tri.get("confidence", 0.0))
                        st.metric("Confidence", f"{round(conf*100)}%")

                    st.write("**Reasoning**")
                    st.write(tri.get("reasoning", ""))

                    if tri.get("concerns"):
                        st.write("**Concerns**")
                        for it in tri["concerns"]:
                            st.write(f"• {it}")

                    if tri.get("next_steps"):
                        st.write("**Next Steps** *(informational, not medical advice)*")
                        for it in tri["next_steps"]:
                            st.write(f"• {it}")

                    # ---------- Anchors (Evidence vs Advisory) ----------
                    anchors = tri.get("anchors", [])
                    if anchors:
                        st.markdown("---")
                        st.markdown("### Source anchors")
                        st.caption("**Legend:** ✅ Evidence = in document (highlightable) • ℹ️ Advisory = model-derived, not in document (not highlightable)")

                        # Normalize + split
                        norm = []
                        for a in anchors:
                            resolved = bool(a.get("resolved", _is_valid_bbox(a.get("bbox")) and a.get("page") is not None))
                            norm.append({
                                "Field": a.get("field"),
                                "Index": a.get("field_index"),
                                "Kind": a.get("kind"),
                                "MatchedBy": a.get("matched_by"),
                                "Snippet": a.get("snippet"),
                                "Page": a.get("page"),
                                "BBox": a.get("bbox"),
                                "Resolved": resolved,
                            })
                        evidence = [r for r in norm if r["Resolved"]]
                        advisory = [r for r in norm if not r["Resolved"]]

                        # ------- Evidence (resolved) -------
                        st.markdown("#### ✅ Evidence (in document)")
                        if evidence:
                            df_ev = pd.DataFrame(evidence, dtype=object)
                            ev_fields = ["all"] + sorted({str(r["Field"]) for r in evidence if r.get("Field") is not None})
                            f_ev = st.selectbox("Filter evidence by field", ev_fields, index=0, key=f"tri_filter_ev_{job.input_path}")
                            if f_ev != "all":
                                df_ev = df_ev[df_ev["Field"] == f_ev]
                            st.dataframe(df_ev, use_container_width=True)

                            # Buttons for visible evidence rows
                            ev_rows = df_ev.to_dict(orient="records")
                            for i, a in enumerate(ev_rows):
                                disabled = (a.get("Page") is None) or (not _is_valid_bbox(a.get("BBox")))
                                if st.button(f"Highlight anchor #{i+1}", key=f"tri_hl_ev_{job.input_path}_{f_ev}_{i}", disabled=disabled):
                                    st.session_state[f"page_{job.input_path}"] = _safe_int(a.get("Page"), 1)
                                    st.session_state[f"hl_{job.input_path}"] = {"page": _safe_int(a.get("Page"), 1), "bbox": a.get("BBox")}
                                    st.rerun()
                        else:
                            st.caption("No direct evidence anchors found in the document.")

                        # ------- Advisory (unresolved) -------
                        st.markdown("#### ℹ️ Advisory & model-derived (not in document)")
                        if advisory:
                            df_adv = pd.DataFrame(advisory, dtype=object)
                            adv_fields = ["all"] + sorted({str(r["Field"]) for r in advisory if r.get("Field") is not None})
                            f_adv = st.selectbox("Filter advisory by field", adv_fields, index=0, key=f"tri_filter_adv_{job.input_path}")
                            if f_adv != "all":
                                df_adv = df_adv[df_adv["Field"] == f_adv]
                            st.dataframe(df_adv, use_container_width=True)
                            st.caption("These rows summarize model reasoning or general guidance; they are not quoted from the PDF and cannot be highlighted.")
                        else:
                            st.caption("No advisory rows.")
                    else:
                        st.caption("No anchors found in the layout index for the triage hints.")

            with tabs[4]:
                meta = {
                    "output_dir": job.output_dir,
                    "layout_json": job.layout_json,
                    "layout_graph_xlsx": job.layout_graph_xlsx,
                    "document_json": job.document_json,
                    "final_json": job.final_json,
                }
                st.json(meta)
                if job.final_json and os.path.exists(job.final_json):
                    with open(job.final_json, "r", encoding="utf-8") as f:
                        st.download_button(
                            "Download final.json",
                            data=f.read(),
                            file_name=os.path.basename(job.final_json),
                            mime="application/json",
                        )

# Ensures final artifacts live under:
# /home/azureuser/llm_demo_org/llm_doc_pipeline/output/<YYYY-MM-DD>/<file_stem>/<seq>/
# by MOVING the DI output contents there (no extra copy left behind).
# Also emits comprehensive triage anchors (urgency, disposition, reasoning, concerns, next steps, numeric, probes),
# with robust VARIANT matching (UK/US spellings, abbreviations, hyphen/slash variants, common rephrasings).

import os, subprocess, sys, json, glob, pathlib, re, shutil
from typing import List, Dict, Any
from datetime import datetime

import pandas as pd
from langgraph.graph import StateGraph, END
from .schemas import PipelineState, FileJob, ClassificationResult
from .llm_client import classify_llm, summarize_llm, extract_entities_llm, triage_llm
from .anchors import build_layout_index, find_snippet_bbox

def _abspath(p): return os.path.abspath(os.path.expanduser(p))

def RESOLVE_DI_SCRIPT(di_script: str) -> str:
    try:
        p = pathlib.Path(di_script).expanduser()
        if p.exists():
            return str(p.resolve())
        root = pathlib.Path(__file__).resolve().parents[1]
        cand = (root / pathlib.Path(di_script)).resolve()
        if cand.exists():
            return str(cand)
        ocr_dir = pathlib.Path(__file__).resolve().parent / "ocr"
        base = pathlib.Path(di_script).name
        cand2 = (ocr_dir / base).resolve()
        if cand2.exists():
            return str(cand2)
    except Exception:
        pass
    return di_script

# ===== Output routing helpers =====
FIXED_OUTPUT_ROOT = "/home/azureuser/llm_demo_org/llm_doc_pipeline/output"

def _ensure_dir(p: pathlib.Path) -> pathlib.Path:
    p.mkdir(parents=True, exist_ok=True)
    return p

def _next_seq_dir(parent: pathlib.Path) -> pathlib.Path:
    existing = []
    if parent.exists():
        for d in parent.iterdir():
            if d.is_dir() and d.name.isdigit():
                try:
                    existing.append(int(d.name))
                except Exception:
                    pass
    nxt = max(existing) + 1 if existing else 1
    return parent / f"{nxt:03d}"

def get_target_output_dir(file_stem: str) -> pathlib.Path:
    today = datetime.now().strftime("%Y-%m-%d")
    date_dir = _ensure_dir(pathlib.Path(FIXED_OUTPUT_ROOT) / today)
    file_dir = _ensure_dir(date_dir / file_stem)
    seq_dir = _ensure_dir(_next_seq_dir(file_dir))
    return seq_dir
# ==================================

def load_scope(scope_xlsx: str):
    x = pd.ExcelFile(scope_xlsx)
    # Classification
    classes = []
    sheet_cls = None
    for s in x.sheet_names:
        if s.strip().lower() == "classification":
            sheet_cls = s
            break
    if sheet_cls is None:
        raise ValueError("Scope workbook is missing a 'Classification' sheet.")

    cls_df = x.parse(sheet_cls)
    dt_col = None
    sub_col = None
    for c in cls_df.columns:
        cl = str(c).strip().lower().replace("_", " ")
        if cl in ("document type", "documenttype", "doc type", "doctype"):
            dt_col = c
        if "sub" in cl and "class" in cl:
            sub_col = c
    if dt_col is None or sub_col is None:
        raise ValueError("Scope 'Classification' sheet must have 'Document Type' and 'Sub Classification'.")

    for _, row in cls_df.iterrows():
        dt = "" if pd.isna(row[dt_col]) else str(row[dt_col]).strip()
        st = "" if pd.isna(row[sub_col]) else str(row[sub_col]).strip()
        if dt:
            classes.append((dt, st))

    # Entity Extraction
    entities_by_type: Dict[str, List[str]] = {}
    sheet_ent = None
    for s in x.sheet_names:
        if s.strip().lower() == "entity extraction":
            sheet_ent = s
            break
    if sheet_ent is None:
        raise ValueError("Scope workbook is missing an 'Entity Extraction' sheet.")

    ent_df = x.parse(sheet_ent)
    ty_col = None
    en_col = None
    for c in ent_df.columns:
        cl = str(c).strip().lower().replace("_", " ")
        if cl in ("document type", "documenttype", "doc type", "doctype"):
            ty_col = c
        if "entity" in cl and "name" in cl:
            en_col = c
    if ty_col is None or en_col is None:
        raise ValueError("Scope 'Entity Extraction' must have 'Document Type' and 'Entity Name'.")

    for _, row in ent_df.iterrows():
        dt = "" if pd.isna(row[ty_col]) else str(row[ty_col]).strip()
        en = "" if pd.isna(row[en_col]) else str(row[en_col]).strip()
        if dt and en:
            entities_by_type.setdefault(dt, []).append(en)

    return classes, entities_by_type

def ingest_files(state: PipelineState) -> PipelineState:
    return state

def run_di(state: PipelineState) -> PipelineState:
    import tempfile, sys, subprocess, pathlib
    script = _abspath(RESOLVE_DI_SCRIPT(state.di_script))
    for job in state.jobs:
        in_path = _abspath(job.input_path)
        out_dir = pathlib.Path(tempfile.mkdtemp(prefix=f"di_{pathlib.Path(in_path).stem}_"))
        completed = subprocess.run(
            [sys.executable, script, "--input", in_path, "--out_dir", str(out_dir)],
            capture_output=True, text=True
        )
        if completed.returncode != 0:
            stderr = (completed.stderr or completed.stdout or "")[:2000]
            raise RuntimeError(f"DI runner failed for {in_path}. stderr: {stderr}")

        lj = out_dir / "layout.json"
        dj = out_dir / "document.json"
        gxl = list(out_dir.glob("*_layout_graph.xlsx"))

        if not lj.exists():
            stderr = (completed.stderr or completed.stdout or "")[:1000]
            raise RuntimeError(f"layout.json missing in DI output for {in_path}. stderr: {stderr}")

        job.output_dir = str(out_dir)
        job.layout_json = str(lj)
        job.document_json = str(dj) if dj.exists() else None
        job.layout_graph_xlsx = str(gxl[0]) if gxl else None
    return state


def _extract_checkbox_info(layout_json_path: str) -> List[Dict[str, Any]]:
    """
    Extract checkbox information from layout JSON to include in document excerpt.
    Returns list of checkbox info with state and label.
    """
    try:
        if not layout_json_path or not os.path.exists(layout_json_path):
            return []
        
        with open(layout_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        checkbox_info = []
        pages = data.get("pages") or data.get("analyzeResult", {}).get("pages") or []
        
        for page in pages:
            # Get selection marks (checkboxes)
            selection_marks = page.get("selectionMarks", []) or []
            words = page.get("words", []) or []
            
            for mark in selection_marks:
                state = mark.get("state", "unselected")  # "selected" or "unselected"
                polygon = mark.get("polygon", [])
                
                if not polygon:
                    continue
                
                # Find nearby text that serves as the checkbox label
                # This is a simplified version - the checkbox_linker.py has more sophisticated logic
                label = _find_checkbox_label(mark, words)
                
                if label:
                    checkbox_info.append({
                        "state": state,
                        "label": label
                    })
        
        return checkbox_info
        
    except Exception as e:
        # If we can't extract checkbox info, return empty list rather than failing
        return []


def _find_checkbox_label(checkbox_mark: Dict[str, Any], words: List[Dict[str, Any]]) -> str:
    """
    Find the text label associated with a checkbox.
    Simplified version of checkbox_linker logic.
    """
    try:
        from .ocr.utils import poly_to_bbox
        
        checkbox_polygon = checkbox_mark.get("polygon", [])
        if not checkbox_polygon:
            return ""
        
        checkbox_bbox = poly_to_bbox(checkbox_polygon)
        if not checkbox_bbox:
            return ""
        
        # Find words near the checkbox (to the right, same line)
        nearby_words = []
        line_tolerance = 0.18  # Same as checkbox_linker
        max_distance = 1.8     # Maximum distance to look for label
        
        for word in words:
            word_polygon = word.get("polygon", [])
            if not word_polygon:
                continue
                
            word_bbox = poly_to_bbox(word_polygon)
            if not word_bbox:
                continue
            
            # Check if word is to the right of checkbox and on same line
            checkbox_center_y = (checkbox_bbox[1] + checkbox_bbox[3]) / 2
            word_center_y = (word_bbox[1] + word_bbox[3]) / 2
            
            # Same line check
            if abs(word_center_y - checkbox_center_y) > line_tolerance:
                continue
            
            # To the right check
            if word_bbox[0] <= checkbox_bbox[2]:  # word starts before checkbox ends
                continue
            
            # Distance check
            if (word_bbox[0] - checkbox_bbox[2]) > max_distance:
                continue
            
            nearby_words.append({
                "text": word.get("content", ""),
                "distance": word_bbox[0] - checkbox_bbox[2],
                "bbox": word_bbox
            })
        
        # Sort by distance and combine nearby words into label
        nearby_words.sort(key=lambda w: w["distance"])
        
        # Take the first few words that are close together
        label_words = []
        last_right = None
        
        for word in nearby_words[:5]:  # Limit to first 5 words
            if not word["text"].strip():
                continue
                
            if last_right is None or (word["bbox"][0] - last_right) <= 0.22:  # Small gap
                label_words.append(word["text"])
                last_right = word["bbox"][2]
            else:
                break
        
        return " ".join(label_words).strip()
        
    except Exception:
        return ""


def parse_artifacts(state: PipelineState) -> PipelineState:
    MAX_CHARS = 16000
    for job in state.jobs:
        index = build_layout_index(job.layout_json, job.layout_graph_xlsx)
        parts = []
        
        # Extract checkbox information from layout JSON
        checkbox_info = _extract_checkbox_info(job.layout_json)
        
        for page in index.get("pages", []):
            if page.get("lines"):
                parts.extend([(ln.get("text") or "").strip() for ln in page["lines"] if (ln.get("text") or "").strip()])
            else:
                parts.extend([w["t"] for w in page.get("words", []) if w.get("t")])
        
        # Add checkbox information to the excerpt
        if checkbox_info:
            parts.append("\n=== CHECKBOX SELECTIONS ===")
            for checkbox in checkbox_info:
                state_symbol = "✓" if checkbox["state"] == "selected" else "☐"
                parts.append(f"{state_symbol} {checkbox['label']}")
        
        excerpt = "\n".join(parts)
        job._excerpt_text = excerpt[:MAX_CHARS]
        job._layout_index = index
    return state

def classify_only(state: PipelineState) -> PipelineState:
    allowed_pairs = [f"{a} ::: {b}" if b else f"{a}" for a,b in state.scope.classes]
    for job in state.jobs:
        job._classification = classify_llm(job._excerpt_text, allowed_pairs)
    return state

def summarize_only(state: PipelineState) -> PipelineState:
    for job in state.jobs:
        job._summary = summarize_llm(job._excerpt_text)
    return state

# ---------- Doc-type probes (broad) ----------
def _probes_for_doc_type(doc_type: str) -> list[str]:
    dt = (doc_type or "").lower()

    # Ambulatory ECG / Zio / Holter
    if any(k in dt for k in ["zio", "holter", "ambulatory", "event monitor", "ecg report", "ambulatory ecg"]):
        return [
            "ventricular tachycardia", "non-sustained vt", "supraventricular tachycardia",
            "atrial fibrillation", "pauses", "av block", "runs",
            "maximum heart rate", "average heart rate",
            "premature ventricular complexes", "premature atrial complexes",
            "bundle branch block",
        ]

    # Exercise stress echo / ESE
    if any(k in dt for k in ["stress echo", "ese", "exercise stress echocardiogram", "stress echocardiogram"]):
        return [
            "equivocal", "ischaemia", "ischemia", "no chest pain",
            "no ischaemic ecg changes", "no ischemic ekg changes",
            "bruce protocol", "good workload",
            "age predicted maximum heart rate",
            "inferior wall", "hypokinesis", "akinesis", "paced rhythm",
        ]

    # CTCA / CT coronary angiography
    if any(k in dt for k in ["ctca", "ct coronary", "coronary angiography (ct)"]):
        return ["cad-rads", "calcium score", "stenosis", "non-calcified plaque", "left main", "lad", "rca", "circumflex"]

    # Resting Echo / TTE
    if any(k in dt for k in ["tte", "transthoracic echo", "echocardiogram", "echo (tte)"]):
        return [
            "left ventricular ejection fraction", "ejection fraction", "ef",
            "mildly impaired lv function", "moderately impaired lv function", "severely impaired lv function",
            "diastolic dysfunction", "valvular regurgitation", "aortic stenosis",
        ]

    # Blood tests / pathology
    if any(k in dt for k in ["blood", "pathology", "lab result"]):
        return ["troponin", "bnp", "cholesterol", "ldl", "hdl", "triglycerides", "creatinine", "egfr"]

    # Referral / clinic letter
    if any(k in dt for k in ["referral", "clinic letter", "gp letter", "consultation letter"]):
        return ["chest pain", "palpitations", "syncope", "blackout", "shortness of breath", "dyspnoea", "dyspnea", "dizziness"]

    return []

# ---------- Decision-support phrases for urgency/disposition ----------
def _decision_support_phrases(doc_type: str, urgency: str, disposition: str) -> dict:
    """
    Heuristics: phrases likely present in the report that support urgency/disposition.
    These are not instructions, just evidence candidates to highlight.
    """
    dt = (doc_type or "").lower()
    u  = (urgency or "").lower()
    d  = (disposition or "").lower()
    out = {"urgency": [], "disposition": []}

    # Stress echo (ESE)
    if any(k in dt for k in ["stress echo", "ese", "exercise stress echocardiogram", "stress echocardiogram"]):
        if u in ("routine", "unknown"):
            out["urgency"] += [
                "no features of inducible ischaemia", "no features of inducible ischemia",
                "no chest pain", "no ischaemic ecg changes", "no ischemic ekg changes",
                "good workload", "achieved % of age predicted",
            ]
        else:
            out["urgency"] += ["features of inducible ischaemia", "ischaemic ecg changes", "chest pain"]

        if d in ("scheduled clinic", "self-care", "unknown"):
            out["disposition"] += [
                "no complications", "complications: no", "no complication",
                "test terminated due to", "test terminated due to fatigue",
                "paced rhythm lowers accuracy", "paced rhythm during exercise",
            ]
        else:
            out["disposition"] += ["high-risk features", "haemodynamic compromise", "hemodynamic compromise"]

    # Ambulatory ECG / Zio / Holter
    if any(k in dt for k in ["zio", "holter", "ambulatory", "event monitor", "ecg report", "ambulatory ecg"]):
        if u in ("urgent", "emergent"):
            out["urgency"] += [
                "ventricular tachycardia", "runs of vt", "sustained vt",
                "atrial fibrillation", "a-fib",
                "pauses", "av block", "atrioventricular block",
            ]
        else:
            out["urgency"] += ["no sustained vt", "no atrial fibrillation", "no a-fib", "no pauses", "no av block"]

        if d in ("scheduled clinic", "same-day clinic"):
            out["disposition"] += ["follow up", "follow-up", "outpatient cardiology", "clinic review"]
        if d == "ed":
            out["disposition"] += ["syncope", "collapse", "haemodynamic compromise", "hemodynamic compromise"]

    # Generic fallbacks
    if not out["urgency"]:
        out["urgency"].append(urgency or "urgency")
    if not out["disposition"]:
        out["disposition"].append(disposition or "disposition")
    return out

# ---------- Phrase VARIANTS (UK/US; abbreviations; hyphen/slash; common rephrasings) ----------
def _variant_phrases(snippet: str) -> list[str]:
    if not snippet or not isinstance(snippet, str):
        return []
    s = snippet.strip().strip(".")
    if not s:
        return []

    variants = []
    def add(v: str):
        v = v.strip()
        if v and v not in variants:
            variants.append(v)

    add(s)

    # UK/US spellings and abbreviations
    pairs = [
        (r"\bischemia\b", "ischaemia"),
        (r"\bischaemia\b", "ischemia"),
        (r"\bdyspnea\b", "dyspnoea"),
        (r"\bdyspnoea\b", "dyspnea"),
        (r"\becg\b", "ekg"),
        (r"\bekg\b", "ecg"),
        (r"\bejection fraction\b", "EF"),
        (r"\bef\b", "ejection fraction"),
        (r"\batrial fibrillation\b", "AF"),
        (r"\bAF\b", "atrial fibrillation"),
        (r"\bventricular tachycardia\b", "VT"),
        (r"\bVT\b", "ventricular tachycardia"),
        (r"\bsupraventricular tachycardia\b", "SVT"),
        (r"\bSVT\b", "supraventricular tachycardia"),
        (r"\bbundle branch block\b", "BBB"),
        (r"\bBBB\b", "bundle branch block"),
        (r"\batrio?ventricular block\b", "AV block"),
        (r"\bAV block\b", "atrioventricular block"),
    ]
    for pat, rep in pairs:
        if re.search(pat, s, flags=re.I):
            add(re.sub(pat, rep, s, flags=re.I))

    # Non-sustained ↔ non sustained; ST-segment/segment
    add(re.sub(r"non[\-\s]?sustained", "non-sustained", s, flags=re.I))
    add(re.sub(r"non[\-\s]?sustained", "non sustained", s, flags=re.I))
    add(re.sub(r"st[\-\s]?segment", "st segment", s, flags=re.I))
    add(re.sub(r"st[\-\s]?segment", "st-segment", s, flags=re.I))

    # Remove frequent qualifiers
    add(re.sub(r"\b(during (test|exercise)|on (test|exercise))\b", "", s, flags=re.I).strip())

    # Slash-splitting e.g., "dyspnoea/fatigue"
    if "/" in s and len(s) <= 64:
        for part in s.split("/"):
            add(part)

    # “No complications” family, “Test terminated due to …”, “paced rhythm … accuracy”
    if re.search(r"complications", s, re.I):
        add("Complications: No")
        add("No complications")
    if re.search(r"test\s+terminated", s, re.I):
        add("test terminated due to")
        add("terminated due to")
    if re.search(r"paced\s+rhythm.*accurac", s, re.I):
        add("presence of paced rhythm does lower accuracy of test")
        add("paced rhythm lowers accuracy")
        add("paced rhythm during exercise")

    # Case-variants
    add(s.lower())
    add(s.title())

    # De-dupe
    return list(dict.fromkeys(variants))

def _try_find_with_variants(idx, phrase: str):
    if not phrase or not idx:
        return None, None
    # try original
    page, bbox = find_snippet_bbox(idx, phrase)
    if page is not None and bbox:
        return page, bbox
    # try variants
    for v in _variant_phrases(phrase):
        page, bbox = find_snippet_bbox(idx, v)
        if page is not None and bbox:
            return page, bbox
    return None, None

# ---------- Triage (anchors with variants + unresolved rows for transparency) ----------
def triage_only(state: PipelineState) -> PipelineState:
    def _dynamic_numeric_phrases(text: str) -> list[str]:
        text = (text or "")
        phrases = []
        # bpm, beats, %, ms, sec, episodes
        for m in re.finditer(r"\b(\d{2,3})\s*bpm\b", text, re.I): phrases.append(f"{m.group(1)} bpm")
        for m in re.finditer(r"\b(\d{1,3})\s*beats?\b", text, re.I): phrases.append(f"{m.group(1)} beats")
        for m in re.finditer(r"\b(\d{2,3})\s*%\b", text): phrases.append(f"{m.group(1)}%")
        for m in re.finditer(r"\b(\d{2,4})\s*ms\b", text, re.I): phrases.append(f"{m.group(1)} ms")
        for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*s(ec|ecs)?\b", text, re.I): phrases.append(f"{m.group(1)} s")
        for m in re.finditer(r"\b(\d{1,3})\s*episodes?\b", text, re.I): phrases.append(f"{m.group(1)} episodes")
        return phrases

    def _split_sentences(s: str) -> list[str]:
        s = (s or "").strip()
        if not s:
            return []
        parts = re.split(r"(?<=[\.\!\?])\s+", s)
        parts = [p.strip() for p in parts if len(p.strip()) >= 6]
        return parts[:2]

    for job in state.jobs:
        tri = triage_llm(job._excerpt_text)

        anchors_out = []
        idx = getattr(job, "_layout_index", None)

        def _emit(snippet: str, kind: str, matched_by: str, field: str, field_index: int | None = None, allow_unresolved: bool = True):
            if not snippet:
                return
            s = snippet.strip()
            if len(s) < 3:
                return
            page, bbox = _try_find_with_variants(idx, s) if idx else (None, None)
            if page is not None and bbox:
                anchors_out.append({
                    "field": field, "field_index": field_index, "kind": kind,
                    "snippet": s, "page": int(page), "bbox": bbox,
                    "matched_by": matched_by, "resolved": True
                })
            elif allow_unresolved:
                anchors_out.append({
                    "field": field, "field_index": field_index, "kind": kind,
                    "snippet": s, "page": None, "bbox": None,
                    "matched_by": matched_by, "resolved": False
                })

        # Doc type for probes / decision support
        doc_type = getattr(getattr(job, "_classification", None), "doc_type", "")

        # Model-provided anchor → reasoning
        if tri.get("anchor_snippet"):
            _emit(tri["anchor_snippet"], "anchor_snippet", "phrase", field="reasoning")

        # Reasoning (1–2 sentences)
        for i, snip in enumerate(_split_sentences(tri.get("reasoning") or "")):
            _emit(snip, "reasoning", "phrase", field="reasoning", field_index=i)

        # Concerns
        for i, c in enumerate(tri.get("concerns") or []):
            _emit(str(c), "concern", "phrase", field="concern", field_index=i)

        # Next steps (often not verbatim; still show as unresolved if we can’t find)
        for i, step in enumerate(tri.get("next_steps") or []):
            _emit(str(step), "next_step", "phrase", field="next_step", field_index=i, allow_unresolved=True)

        # Numeric phrases from reasoning/concerns
        dyn = []
        dyn.extend(_dynamic_numeric_phrases(tri.get("reasoning") or ""))
        for c in (tri.get("concerns") or []):
            dyn.extend(_dynamic_numeric_phrases(str(c)))
        for i, p in enumerate(dict.fromkeys(dyn)):
            _emit(p, "numeric", "numeric", field="numeric", field_index=i, allow_unresolved=False)

        # Decision-support evidence for urgency / disposition
        dsp = _decision_support_phrases(doc_type, tri.get("urgency"), tri.get("disposition"))
        for i, p in enumerate(dsp.get("urgency", [])):
            _emit(p, "decision_support", "decision_support", field="urgency", field_index=i)
        for i, p in enumerate(dsp.get("disposition", [])):
            _emit(p, "decision_support", "decision_support", field="disposition", field_index=i)

        # Doc-type probes
        for i, probe in enumerate(_probes_for_doc_type(doc_type)):
            _emit(probe, "probe", "doc_type", field="probe", field_index=i, allow_unresolved=False)

        tri["anchors"] = anchors_out
        job._triage = tri
    return state
# ----------------------------------------------------------------

def entities_only(state: PipelineState) -> PipelineState:
    for job in state.jobs:
        cls: ClassificationResult = job._classification
        ents = state.scope.entities_by_type.get(cls.doc_type, [])
        er = extract_entities_llm(job._excerpt_text, job._summary.bullets, ents)

        idx = job._layout_index
        for name, ev in list(er.scalar.items()):
            page, bbox = find_snippet_bbox(idx, ev.source_snippet or "")
            if page is None or bbox is None:
                page, bbox = find_snippet_bbox(idx, str(ev.value) if ev.value is not None else "")
            er.scalar[name].page = page
            er.scalar[name].bbox = bbox
        for list_name, rows in list(er.lists.items()):
            for row in rows:
                for k, ev in list(row.items()):
                    page, bbox = find_snippet_bbox(idx, ev.source_snippet or "")
                    if page is None or bbox is None:
                        page, bbox = find_snippet_bbox(idx, str(ev.value) if ev.value is not None else "")
                    row[k].page = page
                    row[k].bbox = bbox

        job._entities = er
    return state

def persist_json(state: PipelineState) -> PipelineState:
    """
    MOVE the DI output contents into:
      /home/azureuser/llm_demo_org/llm_doc_pipeline/output/<YYYY-MM-DD>/<file_stem>/<seq>/
    Then write/overwrite final.json there and update job paths.
    """
    for job in state.jobs:
        file_stem = pathlib.Path(job.input_path).stem
        src_dir = pathlib.Path(job.output_dir) if job.output_dir else None
        target_dir = get_target_output_dir(file_stem)

        if src_dir and src_dir.exists():
            for name in os.listdir(src_dir):
                src_path = src_dir / name
                dst_path = target_dir / name
                try:
                    if dst_path.exists():
                        stem = dst_path.stem
                        suf = dst_path.suffix
                        dst_path = target_dir / f"{stem}_copy{suf}"
                    shutil.move(str(src_path), str(dst_path))
                except Exception:
                    pass
            try:
                shutil.rmtree(src_dir, ignore_errors=True)
            except Exception:
                pass

        gxl = list(target_dir.glob("*_layout_graph.xlsx"))
        lj  = list(target_dir.glob("*_layout.json"))
        dj  = list(target_dir.glob("*_document.json"))
        job.layout_graph_xlsx = str(gxl[0]) if gxl else None
        job.layout_json       = str(lj[0])  if lj  else None
        job.document_json     = str(dj[0])  if dj  else None

        out = {
            "file": job.input_path,
            "classification": job._classification.model_dump(),
            "summary": job._summary.model_dump(),
            "entities": {
                "scalar": {k: v.model_dump() for k, v in job._entities.scalar.items()},
                "lists": [
                    {k: vv.model_dump() for k, vv in row.items()}
                    for list_name, rows in job._entities.lists.items()
                    for row in rows
                ]
            },
            "triage": getattr(job, "_triage", {
                "applicable": False,
                "urgency": "unknown",
                "disposition": "unknown",
                "confidence": 0.0,
                "reasoning": "not available",
                "anchor_snippet": "",
                "concerns": [],
                "next_steps": [],
                "anchors": []
            })
        }
        final = target_dir / "final.json"
        with open(final, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)

        job.output_dir = str(target_dir)
        job.final_json = str(final)

    return state

def build_graph():
    g = StateGraph(PipelineState)
    g.add_node("ingest", ingest_files)
    g.add_node("run_di", run_di)
    g.add_node("parse_artifacts", parse_artifacts)
    g.add_node("classify", classify_only)
    g.add_node("summarize", summarize_only)
    g.add_node("triage", triage_only)
    g.add_node("entities", entities_only)
    g.add_node("persist", persist_json)

    g.set_entry_point("ingest")
    g.add_edge("ingest", "run_di")
    g.add_edge("run_di", "parse_artifacts")
    g.add_edge("parse_artifacts", "classify")
    g.add_edge("classify", "summarize")
    g.add_edge("summarize", "triage")
    g.add_edge("triage", "entities")
    g.add_edge("entities", "persist")
    g.add_edge("persist", END)
    return g.compile()

def run_pipeline(input_files: List[str], di_script: str, scope_xlsx: str) -> PipelineState:
    classes, ents = load_scope(scope_xlsx)
    state = PipelineState(
        jobs=[FileJob(input_path=f) for f in input_files],
        di_script=di_script,
        scope_xlsx=scope_xlsx
    )
    state.scope.classes = classes
    state.scope.entities_by_type = ents

    app = build_graph()
    final_state = app.invoke(state)
    if isinstance(final_state, dict):
        final_state = PipelineState(**final_state)
    return final_state

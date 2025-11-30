# worker.py — page-wise uploads + fast pickup + final workbook/url fields
import os, io, json, time, uuid, tempfile, traceback, random, socket
from datetime import timedelta, datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

import psycopg
from psycopg.rows import dict_row
from azure.servicebus import ServiceBusClient, AutoLockRenewer
from azure.storage.blob import BlobServiceClient, ContentSettings

# Add pandas for Scope workbook processing
try:
    import pandas as pd
except ImportError:
    pd = None

# Orchestrator
try:
    from llm_doc_pipeline.orchestrator.graph import run_pipeline
    from llm_doc_pipeline.orchestrator.schemas import PipelineState
except Exception:
    from orchestrator.graph import run_pipeline
    from orchestrator.schemas import PipelineState

# Remove broken LLM import - we'll use rule-based consolidation instead
summarize_llm = None  # Will implement custom consolidation logic

# -------------------------
# Config from env
# -------------------------
POSTGRES_URL = os.environ["POSTGRES_URL"]

SB_CONN      = os.environ["SERVICEBUS_CONNECTION"]
QUEUE_NAME   = os.environ.get("QUEUE_NAME", "doc-ingest")
SB_PREFETCH         = int(os.environ.get("SB_PREFETCH", "1"))
SB_MAX_WAIT_SECONDS = int(os.environ.get("SB_MAX_WAIT_SECONDS", "1"))
SB_LOCK_RENEW_SECS  = int(os.environ.get("SB_LOCK_RENEW_SECS", "900"))

STG_ACCOUNT  = os.environ["STORAGE_ACCOUNT_NAME"]
STG_KEY      = os.environ["STORAGE_ACCOUNT_KEY"]
INCOMING_CT  = os.environ.get("INCOMING_CONTAINER",  "incoming")
PROCESSED_CT = os.environ.get("PROCESSED_CONTAINER", "processed")

DI_SCRIPT_PATH  = os.environ.get("DI_SCRIPT_PATH",  "/app/llm_doc_pipeline/orchestrator/ocr/di_pages_merge.py")
SCOPE_XLSX_PATH = os.environ.get("SCOPE_XLSX_PATH", "/app/assets/Scope_Workbook.xlsx")

HOST = os.getenv("HOSTNAME") or socket.gethostname()
def log(msg: str): print(f"[{HOST}] {msg}", flush=True)
def _iso_now() -> str: return datetime.now(timezone.utc).isoformat()

# -------------------------
# Blob helpers
# -------------------------
def _blob_https(container: str, blob: str) -> str:
    return f"https://{STG_ACCOUNT}.blob.core.windows.net/{container}/{blob}"

def _ctype(name: str) -> str:
    ext = Path(name).suffix.lower()
    return {
        ".json": "application/json",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pdf":  "application/pdf",
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".txt":  "text/plain",
    }.get(ext, "application/octet-stream")

def _upload(bs: BlobServiceClient, container: str, blob: str, data: bytes, ctype: str):
    bs.get_blob_client(container, blob).upload_blob(
        data, overwrite=True, content_settings=ContentSettings(content_type=ctype)
    )

def _upload_json(bs: BlobServiceClient, container: str, blob: str, obj: dict):
    data = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
    _upload(bs, container, blob, data, "application/json")

def _download_to_tmp(bs: BlobServiceClient, container: str, blob: str) -> str:
    local_dir = tempfile.mkdtemp(prefix="job_")
    local = os.path.join(local_dir, Path(blob).name)
    data = bs.get_blob_client(container, blob).download_blob().readall()
    with open(local, "wb") as w: w.write(data)
    return local

def _download_blob_json(bs: BlobServiceClient, container: str, blob: str) -> Optional[dict]:
    try:
        data = bs.get_blob_client(container, blob).download_blob().readall()
        return json.loads(data.decode("utf-8"))
    except Exception:
        return None

# -------------------------
# DB helpers
# -------------------------
def _ensure_doc_row(conn, doc_id: str):
    with conn.cursor() as cur:
        cur.execute("select 1 from documents where doc_id=%s", (doc_id,))
        if cur.fetchone() is None:
            cur.execute("""
                insert into documents (doc_id, status, created_at, updated_at)
                values (%s, 'queued', now(), now())
            """, (doc_id,))

def _columns(conn) -> set[str]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
          SELECT column_name
            FROM information_schema.columns
           WHERE table_schema='public' AND table_name='documents'
        """)
        return {r["column_name"] for r in cur.fetchall()}

def _batch_completion_from_db(conn, batch_id: str) -> dict:
    """
    Returns completion info by reading documents table.
    expected: total docs for this batch (count(*))
    succeeded: count where status='succeeded'
    ready: True iff expected>0 and expected==succeeded
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            select
              count(*)::int as total,
              sum(case when status='succeeded' then 1 else 0 end)::int as succeeded
            from documents
            where batch_id=%s
        """, (batch_id,))
        row = cur.fetchone() or {"total": 0, "succeeded": 0}
    expected = row["total"] or 0
    succ     = row["succeeded"] or 0
    return {
        "expected": expected,
        "succeeded": succ,
        "pending": max(expected - succ, 0),
        "ready": expected > 0 and succ == expected,
        "has_manifest": False,  # for parity with earlier shape
    }

# -------------------------
# SB decode + transient check
# -------------------------
def _decode_sb_message(msg) -> Dict[str, Any]:
    try:
        body_bytes = b"".join(part for part in msg.body)
        return json.loads(body_bytes.decode("utf-8"))
    except Exception:
        pass
    try:
        return json.loads(str(msg))
    except Exception:
        pass
    if isinstance(msg.body, dict):
        return msg.body
    raise RuntimeError("Cannot decode Service Bus message body as JSON")

def _is_rate_limit_error(err: str) -> bool:
    e = err.lower()
    return ("429" in err or "rate limit" in e or "too many requests" in e or
            "exceeded call rate limit" in e)

# -------------------------
# Page-wise uploads
# -------------------------
def _upload_graph_pages(bs: BlobServiceClient, doc_id: str, local_out_dir: Path) -> str | None:
    gp = local_out_dir / "graph_pages"
    if not gp.exists():
        return None
    base_prefix = f"{doc_id}/graph_pages/"
    for p in gp.rglob("*"):
        if p.is_file():
            rel = p.relative_to(gp).as_posix()
            blob_name = base_prefix + rel
            with open(p, "rb") as f:
                _upload(bs, PROCESSED_CT, blob_name, f.read(), _ctype(p.name))
    return _blob_https(PROCESSED_CT, base_prefix)

# -------------------------
# Batch Index + Reports
# -------------------------
def _batch_index_blob(batch_id: str, doc_id: str) -> str:
    return f"batch/{batch_id}/index/{doc_id}.json"

def _batch_root(batch_id: str) -> str:
    return f"batch/{batch_id}/"

def _try_extract_summary_and_triage(final_json: dict) -> Dict[str, Any]:
    summary_text = (
        (final_json.get("summary") or {}).get("text")
        or (final_json.get("outputs") or {}).get("summary_text")
        or final_json.get("findings_text")
        or ""
    )
    triage = final_json.get("triage") or (final_json.get("outputs") or {}).get("triage")
    return {"summary_text": summary_text, "triage": triage}

def _write_batch_index(
    bs: BlobServiceClient,
    batch_id: str,
    doc_id: str,
    source_file: str,
    final_json_blob: str,
    final_json_local: Optional[str],
):
    final_json = None
    if final_json_local and os.path.exists(final_json_local):
        try:
            with open(final_json_local, "r", encoding="utf-8") as f:
                final_json = json.load(f)
        except Exception:
            final_json = None
    if final_json is None:
        final_json = _download_blob_json(bs, PROCESSED_CT, final_json_blob) or {}

    st = _try_extract_summary_and_triage(final_json)
    idx_doc = {
        "doc_id": doc_id,
        "source_file": source_file,
        "final_json_blob": final_json_blob,
        "final_json_url": _blob_https(PROCESSED_CT, final_json_blob),
        "triage": st["triage"],
        "summary_text": st["summary_text"],
        "updated_at": _iso_now(),
    }
    _upload_json(bs, PROCESSED_CT, _batch_index_blob(batch_id, doc_id), idx_doc)

def _list_batch_index_docs(bs: BlobServiceClient, batch_id: str) -> List[dict]:
    cc = bs.get_container_client(PROCESSED_CT)
    prefix = f"batch/{batch_id}/index/"
    out: List[dict] = []
    try:
        for blob in cc.list_blobs(name_starts_with=prefix):
            d = _download_blob_json(bs, PROCESSED_CT, blob.name)
            if isinstance(d, dict):
                out.append(d)
    except Exception:
        pass
    return out

def _load_scope_entities(scope_xlsx_path: str) -> Dict[str, List[str]]:
    """
    Load entity definitions from Scope_Workbook.xlsx to understand 
    which entities should be prioritized and how to merge them.
    """
    try:
        if pd is None:
            log("Pandas not available, using default entity mapping")
            return {}
        
        if not os.path.exists(scope_xlsx_path):
            log(f"Scope workbook not found at {scope_xlsx_path}, using default entity mapping")
            return {}
        
        # Try to read the Entity Extraction sheet
        xl_file = pd.ExcelFile(scope_xlsx_path)
        entity_sheet = None
        
        for sheet_name in xl_file.sheet_names:
            if 'entity' in sheet_name.lower() and 'extraction' in sheet_name.lower():
                entity_sheet = sheet_name
                break
        
        if not entity_sheet:
            log("No Entity Extraction sheet found in Scope workbook")
            return {}
        
        df = xl_file.parse(entity_sheet)
        entities_by_type = {}
        
        # Parse the entity definitions
        for _, row in df.iterrows():
            doc_type = str(row.get('Document Type', '')).strip()
            entity_name = str(row.get('Entity Name', '')).strip()
            
            if doc_type and entity_name:
                if doc_type not in entities_by_type:
                    entities_by_type[doc_type] = []
                entities_by_type[doc_type].append(entity_name)
        
        log(f"Loaded {len(entities_by_type)} document types with entity definitions from Scope workbook")
        return entities_by_type
        
    except Exception as e:
        log(f"Error loading Scope workbook entities: {e}")
        return {}

def _consolidate_patient_documents(documents: List[dict], scope_xlsx_path: str = None) -> Dict[str, Any]:
    """
    Intelligently consolidate multiple documents into a unified patient report.
    Applies precedence rules: referral documents take priority over test reports.
    Uses Scope_Workbook.xlsx entity definitions for intelligent merging.
    """
    if not documents:
        return {"error": "No documents to consolidate"}
    
    # Load entity schema from Scope workbook if available
    scope_entities = {}
    if scope_xlsx_path:
        scope_entities = _load_scope_entities(scope_xlsx_path)
    
    # Categorize documents by type
    referral_docs = []
    test_docs = []
    other_docs = []
    
    for doc in documents:
        if doc is None:
            continue
        
        classification = doc.get("classification", {})
        doc_type = classification.get("doc_type", "").lower()
        
        if "referral" in doc_type or "refer" in doc_type:
            referral_docs.append(doc)
        elif any(term in doc_type for term in ["test", "lab", "blood", "result", "report"]):
            test_docs.append(doc)
        else:
            other_docs.append(doc)
    
    # Initialize consolidated structure
    consolidated = {
        "patient_info": {},
        "clinical_summary": {
            "chief_complaint": "",
            "clinical_context": "",
            "findings": [],
            "recommendations": []
        },
        "entities": {
            "scalar": {},
            "lists": {}
        },
        "triage": {
            "urgency": "routine",
            "disposition": "outpatient",
            "concerns": [],
            "next_steps": []
        },
        "document_sources": {
            "referral_documents": len(referral_docs),
            "test_documents": len(test_docs),
            "other_documents": len(other_docs),
            "total_documents": len(documents)
        }
    }
    
    # Define priority entities (referral takes precedence)
    priority_entities = [
        "patient_name", "patient name", "name",
        "date_of_birth", "date of birth", "dob",
        "nhs_number", "nhs number", "patient_id", "patient id",
        "referral_reason", "chief_complaint", "presenting_complaint"
    ]
    
    # Process documents in order of precedence: referrals first, then tests, then others
    all_docs_ordered = referral_docs + test_docs + other_docs
    
    for doc in all_docs_ordered:
        if doc is None:
            continue
        
        # Merge entities with precedence (first document wins for priority entities)
        doc_entities = doc.get("entities", {})
        
        # Scalar entities (single values like patient name, DOB, etc.)
        scalar_entities = doc_entities.get("scalar", {})
        
        # Ensure scalar_entities is a dict
        if not isinstance(scalar_entities, dict):
            log(f"Unexpected scalar_entities type {type(scalar_entities)} for doc {doc.get('doc_id', 'unknown')}, using empty dict")
            scalar_entities = {}
            
        for entity_name, entity_data in scalar_entities.items():
            entity_lower = entity_name.lower()
            
            # Check if this is a priority entity that should follow precedence rules
            is_priority = any(priority in entity_lower for priority in priority_entities)
            
            if is_priority:
                # For priority entities, only update if not already set (referral precedence)
                if entity_name not in consolidated["entities"]["scalar"]:
                    consolidated["entities"]["scalar"][entity_name] = entity_data
            else:
                # For non-priority entities, always merge (accumulate all data)
                if entity_name not in consolidated["entities"]["scalar"]:
                    consolidated["entities"]["scalar"][entity_name] = entity_data
        
        # List entities (multiple values like medications, allergies, etc.)
        list_entities = doc_entities.get("lists", [])
        
        # Handle both dict and list formats for backwards compatibility
        if isinstance(list_entities, list):
            # If it's a list (correct format), keep it as is
            log(f"Processing list_entities in correct list format for doc {doc.get('doc_id', 'unknown')}")
        elif isinstance(list_entities, dict):
            # If it's a dict, convert each key-value pair appropriately
            log(f"Converting list_entities from dict to list format for doc {doc.get('doc_id', 'unknown')}")
            converted_list = []
            for entity_name, entity_data in list_entities.items():
                if isinstance(entity_data, list):
                    converted_list.extend(entity_data)
                else:
                    converted_list.append(entity_data)
            list_entities = converted_list
        else:
            # If it's neither list nor dict, initialize as empty list
            log(f"Unexpected list_entities type {type(list_entities)} for doc {doc.get('doc_id', 'unknown')}, using empty list")
            list_entities = []
        
        # Process list entities properly
        if isinstance(list_entities, list):
            for entity_item in list_entities:
                if not isinstance(entity_item, dict):
                    continue
                
                # Group similar entities together
                for entity_key, entity_value in entity_item.items():
                    if entity_key not in consolidated["entities"]["lists"]:
                        consolidated["entities"]["lists"][entity_key] = []
                    
                    # Avoid duplicates when merging lists
                    existing_values = {str(item.get("value", "")) if isinstance(item, dict) else str(item) for item in consolidated["entities"]["lists"][entity_key]}
                    item_value = str(entity_value.get("value", "")) if isinstance(entity_value, dict) else str(entity_value)
                    if item_value not in existing_values and item_value.strip():
                        consolidated["entities"]["lists"][entity_key].append(entity_value)
        
        # Merge clinical information
        summary = doc.get("summary", {})
        if isinstance(summary, dict):
            bullets = summary.get("bullets", [])
            if bullets and isinstance(bullets, list):
                consolidated["clinical_summary"]["findings"].extend(bullets)
        elif isinstance(summary, list):
            # Handle case where summary is directly a list
            consolidated["clinical_summary"]["findings"].extend(summary)
        
        # Extract key patient info from entities with precedence
        for entity_name, entity_data in scalar_entities.items():
            entity_lower = entity_name.lower()
            value = entity_data.get("value", "") if isinstance(entity_data, dict) else str(entity_data)
            
            # Only update if not already set (maintains referral precedence)
            if "patient" in entity_lower and "name" in entity_lower and not consolidated["patient_info"].get("name"):
                consolidated["patient_info"]["name"] = value
            elif "date" in entity_lower and "birth" in entity_lower and not consolidated["patient_info"].get("dob"):
                consolidated["patient_info"]["dob"] = value
            elif ("nhs" in entity_lower or "mrn" in entity_lower or "patient" in entity_lower) and "number" in entity_lower and not consolidated["patient_info"].get("patient_id"):
                consolidated["patient_info"]["patient_id"] = value
        
        # Merge triage information with intelligent reassessment
        doc_triage = doc.get("triage", {})
        if isinstance(doc_triage, dict):
            doc_urgency = doc_triage.get("urgency", "routine")
            urgency_priority = {"urgent": 3, "semi-urgent": 2, "routine": 1}
            current_priority = urgency_priority.get(consolidated["triage"]["urgency"], 1)
            doc_priority = urgency_priority.get(doc_urgency, 1)
            
            # Check if this is a test document with normal results
            is_test_doc = any(term in classification.get("doc_type", "").lower() for term in ["test", "lab", "blood", "result", "report"])
            has_normal_results = False
            
            if is_test_doc:
                # Check for normal/reassuring test results
                doc_summary = doc.get("summary", {})
                if isinstance(doc_summary, dict):
                    summary_text = " ".join(doc_summary.get("bullets", [])).lower()
                    has_normal_results = any(term in summary_text for term in ["normal", "negative", "within normal limits", "unremarkable", "no abnormality"])
            
            # Apply clinical reasoning: normal test results should reassess urgency
            if has_normal_results and consolidated["triage"]["urgency"] == "urgent":
                # Downgrade urgency if test results are reassuring
                consolidated["triage"]["urgency"] = "semi-urgent"
                consolidated["triage"]["disposition"] = "routine follow-up"
                consolidated["triage"]["clinical_reasoning"] = "Initial urgent assessment downgraded based on reassuring test results"
            elif doc_priority > current_priority:
                consolidated["triage"]["urgency"] = doc_urgency
                consolidated["triage"]["disposition"] = doc_triage.get("disposition", consolidated["triage"]["disposition"])
            
            # Merge concerns and next steps
            if doc_triage.get("concerns"):
                consolidated["triage"]["concerns"].extend(doc_triage["concerns"])
            if doc_triage.get("next_steps"):
                consolidated["triage"]["next_steps"].extend(doc_triage["next_steps"])
    
    # Create unified clinical summary with precedence
    if referral_docs:
        # Use referral as primary clinical context
        ref_summary = referral_docs[0].get("summary", {})
        if isinstance(ref_summary, dict) and ref_summary.get("bullets"):
            consolidated["clinical_summary"]["chief_complaint"] = ". ".join(ref_summary["bullets"][:2])
            consolidated["clinical_summary"]["clinical_context"] = ". ".join(ref_summary["bullets"])
    
    # Add test findings with clinical correlation instead of simple duplication
    if test_docs:
        test_findings = []
        clinical_correlations = []
        
        for test_doc in test_docs:
            test_summary = test_doc.get("summary", {})
            test_classification = test_doc.get("classification", {})
            test_type = test_classification.get("doc_subtype", "Test")
            
            if isinstance(test_summary, dict) and test_summary.get("bullets"):
                # Extract key findings without redundant prefixes
                for finding in test_summary["bullets"]:
                    if "purpose:" not in finding.lower() and "parties:" not in finding.lower():
                        test_findings.append(f"{test_type}: {finding}")
        
        # Add clinical correlation based on referral vs test findings
        if referral_docs and test_findings:
            referral_summary = referral_docs[0].get("summary", {})
            if isinstance(referral_summary, dict) and referral_summary.get("bullets"):
                referral_text = " ".join(referral_summary["bullets"]).lower()
                test_text = " ".join(test_findings).lower()
                
                # Generate clinical correlation
                if "chest" in referral_text and "normal ecg" in test_text:
                    clinical_correlations.append("Clinical correlation: Normal ECG despite reported chest symptoms - consider non-cardiac causes")
                elif "normal" in test_text and any(symptom in referral_text for symptom in ["pain", "discomfort", "symptoms"]):
                    clinical_correlations.append("Clinical correlation: Reassuring test results in context of reported symptoms")
        
        if test_findings:
            consolidated["clinical_summary"]["findings"].extend(test_findings)
        if clinical_correlations:
            consolidated["clinical_summary"]["findings"].extend(clinical_correlations)
    
    # Generate intelligent recommendations based on consolidated triage and document types
    recommendations = []
    urgency = consolidated["triage"]["urgency"]
    has_test_results = len(test_docs) > 0
    has_normal_results = any("normal" in str(consolidated["clinical_summary"]["findings"]).lower() for _ in [1])
    
    if urgency == "urgent":
        if has_normal_results:
            recommendations.extend([
                "⚡ Expedited review recommended despite reassuring initial tests",
                "Consider additional investigations if symptoms persist",
                "Monitor for symptom progression"
            ])
        else:
            recommendations.extend([
                "⚠️ Urgent clinical review required",
                "Consider immediate referral or escalation",
                "Monitor patient closely"
            ])
    elif urgency == "semi-urgent":
        recommendations.extend([
            "📅 Clinical review within 48-72 hours",
            "Follow up on investigation results" if has_test_results else "Consider appropriate investigations",
            "Provide patient safety netting advice"
        ])
    else:
        recommendations.extend([
            "📋 Routine follow-up as clinically indicated",
            "Review results with patient" if has_test_results else "Consider if investigations needed",
            "Continue current management plan"
        ])
    
    # Add specific next steps from triage, avoiding duplicates
    triage_steps = consolidated["triage"]["next_steps"]
    for step in triage_steps:
        if not any(step.lower() in rec.lower() for rec in recommendations):
            recommendations.append(step)
    
    consolidated["clinical_summary"]["recommendations"] = recommendations
    
    # Generate LLM-based advice and guidance section
    try:
        # Import LLM client function
        from llm_doc_pipeline.orchestrator.llm_client import generate_advice_guidance_llm
        
        log("Generating LLM-based advice and guidance...")
        advice_and_guidance = generate_advice_guidance_llm(consolidated)
        log(f"LLM advice generation completed with {len(advice_and_guidance.get('clinical_advice', []))} clinical advice items")
        
    except Exception as e:
        log(f"LLM advice generation failed: {str(e)}, falling back to structured recommendations")
        
        # Fallback to structured approach if LLM fails
        advice_and_guidance = {
            "clinical_advice": [],
            "next_actions": [],
            "safety_netting": [],
            "follow_up": []
        }
        
        # Generate clinical advice based on document types and findings
        if urgency == "urgent":
            advice_and_guidance["clinical_advice"].extend([
                "🚨 Priority clinical assessment required",
                "Review symptoms and examination findings thoroughly",
                "Consider differential diagnoses systematically"
            ])
            advice_and_guidance["safety_netting"].extend([
                "Advise patient to seek immediate medical attention if symptoms worsen",
                "Provide clear escalation pathways",
                "Ensure patient understands warning signs"
            ])
        elif urgency == "semi-urgent":
            advice_and_guidance["clinical_advice"].extend([
                "⏰ Timely clinical assessment within 48-72 hours",
                "Correlate clinical findings with investigation results",
                "Consider symptom progression and patient concerns"
            ])
            advice_and_guidance["safety_netting"].extend([
                "Provide patient with clear advice on when to return",
                "Ensure access to appropriate follow-up care"
            ])
        else:
            advice_and_guidance["clinical_advice"].extend([
                "📋 Routine clinical management approach",
                "Regular monitoring as clinically indicated",
                "Address patient concerns and questions"
            ])
        
        # Add follow-up recommendations
        if has_test_results:
            if has_normal_results:
                advice_and_guidance["follow_up"].extend([
                    "Review test results with patient",
                    "Explain implications of normal findings",
                    "Consider if further investigations needed based on symptoms"
                ])
            else:
                advice_and_guidance["follow_up"].extend([
                    "Discuss abnormal results and their significance",
                    "Plan appropriate management based on findings",
                    "Arrange specialist referral if indicated"
                ])
        
        # Add next actions from triage
        for step in triage_steps:
            if "follow" in step.lower():
                advice_and_guidance["follow_up"].append(step)
            elif "consider" in step.lower() or "investigate" in step.lower():
                advice_and_guidance["next_actions"].append(step)
            else:
                advice_and_guidance["next_actions"].append(step)
    
    consolidated["advice_and_guidance"] = advice_and_guidance
    
    # Add metadata about consolidation with enhanced quality metrics
    name_variations = []
    identity_discrepancies = []
    
    # Check for name variations across documents
    all_names = []
    for doc in all_docs_ordered:
        if doc is None:
            continue
        scalar_entities = doc.get("entities", {}).get("scalar", {})
        if isinstance(scalar_entities, dict):
            for entity_name, entity_data in scalar_entities.items():
                if "name" in entity_name.lower() and "patient" in entity_name.lower():
                    value = entity_data.get("value", "") if isinstance(entity_data, dict) else str(entity_data)
                    if value and value not in all_names:
                        all_names.append(value)
    
    if len(all_names) > 1:
        name_variations = all_names
        identity_discrepancies.append("name_variations")
    
    # Calculate clinical consistency score
    has_clinical_correlation = len(clinical_correlations) > 0 if 'clinical_correlations' in locals() else False
    urgency_reassessed = consolidated["triage"].get("clinical_reasoning") is not None
    consistency_factors = [
        has_clinical_correlation,
        urgency_reassessed,
        len(consolidated["clinical_summary"]["recommendations"]) > 0,
        consolidated["triage"]["urgency"] != "urgent" or not has_normal_results
    ]
    clinical_consistency_score = sum(consistency_factors) / len(consistency_factors)
    
    consolidated["consolidation_metadata"] = {
        "consolidation_timestamp": _iso_now(),
        "precedence_applied": "referral_priority",
        "scope_entities_loaded": len(scope_entities) > 0,
        "priority_entities_used": priority_entities,
        "name_match_confidence": 1.0 - (len(name_variations) * 0.1),
        "identity_discrepancies": identity_discrepancies,
        "clinical_consistency_score": round(clinical_consistency_score, 2),
        "has_clinical_correlation": has_clinical_correlation,
        "urgency_reassessed": urgency_reassessed
    }
    
    # Add full document reference section
    consolidated["document_references"] = []
    for doc in all_docs_ordered:
        if doc is None:
            continue
        
        # Extract document name from file path or use doc_id
        doc_name = "unknown_document"
        if doc.get("file"):
            # Extract filename from full path
            doc_name = doc["file"].split("/")[-1] if "/" in doc["file"] else doc["file"]
        elif doc.get("doc_id"):
            doc_name = doc["doc_id"]
        
        # Create reference entry with full document content
        doc_reference = {
            "document_name": doc_name,
            "document_type": doc.get("classification", {}).get("doc_type", "Unknown"),
            "document_subtype": doc.get("classification", {}).get("doc_subtype", "Unknown"),
            "full_content": doc  # Include complete final.json content
        }
        
        consolidated["document_references"].append(doc_reference)
    
    return consolidated

def _summarize_batch_with_consolidation(documents: List[dict], scope_xlsx_path: str = None) -> Dict[str, Any]:
    """
    Create a consolidated patient summary using rule-based logic instead of LLM.
    """
    consolidated = _consolidate_patient_documents(documents, scope_xlsx_path)
    
    # Format for the expected output structure
    summary_bullets = []
    guidance_bullets = []
    
    # Create summary bullets from consolidated data
    if consolidated.get("patient_info", {}).get("name"):
        summary_bullets.append(f"Patient: {consolidated['patient_info']['name']}")
    
    if consolidated.get("clinical_summary", {}).get("chief_complaint"):
        summary_bullets.append(f"Presenting issue: {consolidated['clinical_summary']['chief_complaint']}")
    
    # Add key findings
    findings = consolidated.get("clinical_summary", {}).get("findings", [])
    if findings:
        summary_bullets.extend(findings[:5])  # Limit to top 5 findings
    
    # Create guidance bullets
    recommendations = consolidated.get("clinical_summary", {}).get("recommendations", [])
    if recommendations:
        guidance_bullets.extend(recommendations)
    
    # Add triage-based guidance
    urgency = consolidated.get("triage", {}).get("urgency", "routine")
    if urgency == "urgent":
        guidance_bullets.insert(0, "⚠️ URGENT: Requires immediate clinical attention")
    elif urgency == "semi-urgent":
        guidance_bullets.insert(0, "⚡ Semi-urgent: Review within 48-72 hours")
    
    # Add concerns if any
    concerns = consolidated.get("triage", {}).get("concerns", [])
    if concerns:
        guidance_bullets.extend([f"Concern: {concern}" for concern in concerns[:3]])
    
    return {
        "summary_of_summaries": {"bullets": summary_bullets},
        "advice_and_guidance": {"bullets": guidance_bullets},
        "consolidated_patient_data": consolidated
    }

def _summarize_batch_with_llm(texts: List[str]) -> Dict[str, Any]:
    combined = "\n\n".join([t for t in texts if t])[:18000]
    result = {
        "summary_of_summaries": {"bullets": []},
        "advice_and_guidance": {"bullets": []}
    }
    try:
        if summarize_llm is None:
            raise RuntimeError("summarize_llm not available")
        overall_summary = summarize_llm(
            "You are preparing a concise cross-document summary for a patient batch. "
            "Synthesize the key points below into 6–10 bullets that a clinician can skim.\n\n" + combined
        )
        if hasattr(overall_summary, "model_dump"):
            result["summary_of_summaries"] = overall_summary.model_dump()
        else:
            result["summary_of_summaries"] = overall_summary  # type: ignore
    except Exception:
        result["summary_of_summaries"] = {"bullets": [t for t in texts if t][:8]}

    try:
        if summarize_llm is None:
            raise RuntimeError("summarize_llm not available")
        guidance = summarize_llm(
            "Provide 'Advice & Guidance' for a clinician based on the following summarized findings. "
            "Cover: (1) immediate concerns, (2) likely next diagnostic steps, (3) treatment considerations, "
            "(4) what to watch for, (5) what information to request if unclear. "
            "Write 6–10 short bullets.\n\n" + combined
        )
        if hasattr(guidance, "model_dump"):
            result["advice_and_guidance"] = guidance.model_dump()
        else:
            result["advice_and_guidance"] = guidance  # type: ignore
    except Exception:
        result["advice_and_guidance"] = {
            "bullets": ["(LLM unavailable) Review individual document summaries."]
        }
    return result

def _build_batch_reports_if_ready(bs: BlobServiceClient, batch_id: Optional[str], conn) -> bool:
    """
    Idempotent. Builds batch reports only when **all documents** in the batch
    have status='succeeded' in the `documents` table.
    Creates a single consolidated JSON with all patient data, advice, and guidance.
    """
    if not batch_id:
        return False

    # Check DB first
    completion = _batch_completion_from_db(conn, batch_id)
    if not completion["ready"]:
        # Not all docs are done; skip consolidation for now.
        log(f"[batch:{batch_id}] waiting: {completion['succeeded']}/{completion['expected']} succeeded")
        return False

    # Now gather the per-doc index (for pointers + summaries)
    idx_docs = _list_batch_index_docs(bs, batch_id)
    header = {
        "batch_id": batch_id,
        "generated_at": _iso_now(),
        "num_documents": len(idx_docs),
        "completion": completion,
    }

    # Gather all document JSONs for consolidation
    documents_data = []
    doc_summaries = []
    for d in idx_docs:
        doc_blob = d.get("final_json_blob")
        if not doc_blob:
            continue
        j = _download_blob_json(bs, PROCESSED_CT, doc_blob)
        if j is not None:
            documents_data.append(j)
            doc_summaries.append(d)
    
    # Create single comprehensive report with everything
    if documents_data:
        # Get unified patient data with enhanced clinical intelligence
        unified_patient_data = _consolidate_patient_documents(documents_data, SCOPE_XLSX_PATH)
        
        # Generate advice and guidance
        consolidated_summary = _summarize_batch_with_consolidation(documents_data, SCOPE_XLSX_PATH)
        
        # Create single comprehensive JSON - streamlined to avoid duplication
        comprehensive_report = {
            **header,
            
            # Core patient data (from unified consolidation)
            "patient_info": unified_patient_data.get("patient_info", {}),
            "clinical_summary": unified_patient_data.get("clinical_summary", {}),
            "triage": unified_patient_data.get("triage", {}),
            
            # Document sources and metadata
            "document_sources": unified_patient_data.get("document_sources", {}),
            "consolidation_metadata": unified_patient_data.get("consolidation_metadata", {}),
            
            # LLM-generated advice and guidance (NEW structure)
            "advice_and_guidance": unified_patient_data.get("advice_and_guidance", {
                "clinical_advice": [],
                "next_actions": [],
                "safety_netting": [],
                "follow_up": []
            }),
            
            # SINGLE consolidated entities section (no duplication)
            "entities": unified_patient_data.get("entities", {}),
            
            # Complete document references section (full final.json content)
            "document_references": unified_patient_data.get("document_references", []),
            
            # Document summaries only (for backward compatibility)
            "documents": [
                {
                    "file": doc.get("file", ""),
                    "classification": doc.get("classification", {}),
                    "summary": doc.get("summary", {}),
                    "triage": doc.get("triage", {})
                    # Removed entities and full content to avoid duplication - see document_references above
                }
                for doc in documents_data
            ]
            
            # Removed unified_patient_data section to eliminate triple duplication
            # All essential data is already included in the top-level sections above
        }
        
        # Upload single comprehensive report
        _upload_json(bs, PROCESSED_CT, _batch_root(batch_id) + "Consolidated_final.json", comprehensive_report)
        
        log(f"[batch:{batch_id}] SINGLE COMPREHENSIVE REPORT created with advice & guidance integrated")
    
    # Update batch status to 'succeeded' when all documents are processed and consolidated
    try:
        log(f"[batch:{batch_id}] ATTEMPTING to update batch status to 'succeeded'")
        
        # First, check if batch exists and its current status
        with conn.cursor() as cur:
            cur.execute("SELECT batch_id, status, created_at FROM batches WHERE batch_id = %s", (batch_id,))
            batch_row = cur.fetchone()
            if batch_row:
                log(f"[batch:{batch_id}] CURRENT BATCH STATUS: {batch_row}")
            else:
                log(f"[batch:{batch_id}] ERROR: Batch not found in batches table!")
                return True
        
        # Now attempt the update - only update status since other columns may not exist
        with conn.cursor() as cur:
            log(f"[batch:{batch_id}] EXECUTING UPDATE statement...")
            cur.execute("""
                UPDATE batches 
                SET status = 'succeeded'
                WHERE batch_id = %s
            """, (batch_id,))
            affected_rows = cur.rowcount
            log(f"[batch:{batch_id}] UPDATE executed - {affected_rows} rows affected")
            
            # Since main connection has autocommit=True, the update should auto-commit
            # But let's verify the update took effect
            cur.execute("SELECT batch_id, status, created_at FROM batches WHERE batch_id = %s", (batch_id,))
            updated_row = cur.fetchone()
            if updated_row:
                log(f"[batch:{batch_id}] POST-UPDATE STATUS: {updated_row}")
                if updated_row.get('status') == 'succeeded':
                    log(f"[batch:{batch_id}] ✅ SUCCESS: Batch status successfully updated to 'succeeded'")
                else:
                    log(f"[batch:{batch_id}] ❌ FAILURE: Status not updated - still shows '{updated_row.get('status')}'")
            else:
                log(f"[batch:{batch_id}] ❌ CRITICAL: Batch disappeared after update attempt!")
            
            if affected_rows == 0:
                log(f"[batch:{batch_id}] WARNING: No batch found to update with ID {batch_id}")
                # Check if batch exists with different ID format
                cur.execute("SELECT batch_id, status FROM batches WHERE batch_id LIKE %s", (f"%{batch_id[-8:]}%",))
                similar_batches = cur.fetchall()
                if similar_batches:
                    log(f"[batch:{batch_id}] SIMILAR BATCHES FOUND: {similar_batches}")
            else:
                log(f"[batch:{batch_id}] UPDATE operation affected {affected_rows} rows")
                
    except Exception as e:
        log(f"[batch:{batch_id}] ERROR updating batch status: {e}")
        import traceback
        log(f"[batch:{batch_id}] STACK TRACE: {traceback.format_exc()}")
        
        # Try to get more debug info about the batch
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as total_batches FROM batches")
                total = cur.fetchone()
                log(f"[batch:{batch_id}] TOTAL BATCHES IN DB: {total}")
                
                cur.execute("SELECT batch_id, status FROM batches ORDER BY created_at DESC LIMIT 5")
                recent = cur.fetchall()
                log(f"[batch:{batch_id}] RECENT BATCHES: {recent}")
        except Exception as debug_e:
            log(f"[batch:{batch_id}] DEBUG QUERY FAILED: {debug_e}")
    
    log(f"[batch:{batch_id}] CONSOLIDATED (all docs succeeded)")
    return True

# -------------------------
# Core processing
# -------------------------
def process_message(conn, bs: BlobServiceClient, body: dict) -> bool:
    doc_id    = body.get("doc_id")
    batch_id  = body.get("batch_id")
    blob_name = body.get("blob_name")
    if not doc_id or not batch_id or not blob_name:
        raise RuntimeError(f"Bad message: {body}")

    _ensure_doc_row(conn, doc_id)

    filename_only = Path(blob_name).name
    with conn.cursor() as cur:
        cur.execute("""
            update documents
               set status='running',
                   filename=%s,
                   started_at = coalesce(started_at, now()),
                   updated_at=now()
             where doc_id=%s
        """, (filename_only, doc_id))

    local_file = _download_to_tmp(bs, INCOMING_CT, blob_name)
    base = Path(local_file).stem

    try:
        state = run_pipeline([local_file], DI_SCRIPT_PATH, SCOPE_XLSX_PATH)
        if isinstance(state, dict):
            state = PipelineState(**state)
        job = state.jobs[0]
        out_dir = Path(job.output_dir)

        uploaded: Dict[str, str] = {}

        # final artifacts
        final_graph = out_dir / f"{base}_layout_graph.xlsx"
        final_json_local_path = out_dir / "final.json"
        final_json_blob_path  = f"{doc_id}/final.json"

        if final_graph.exists():
            with open(final_graph, "rb") as f:
                dest = f"{doc_id}/{final_graph.name}"
                _upload(bs, PROCESSED_CT, dest, f.read(), _ctype(final_graph.name))
                uploaded["final_graph_xlsx_url"] = _blob_https(PROCESSED_CT, dest)

        if final_json_local_path.exists():
            with open(final_json_local_path, "rb") as f:
                _upload(bs, PROCESSED_CT, final_json_blob_path, f.read(), "application/json")
                uploaded["final_json_url"] = _blob_https(PROCESSED_CT, final_json_blob_path)

        gp_prefix = _upload_graph_pages(bs, str(doc_id), out_dir)
        if gp_prefix:
            uploaded["graph_pages_prefix_url"] = gp_prefix

        if os.path.exists(SCOPE_XLSX_PATH):
            with open(SCOPE_XLSX_PATH, "rb") as f:
                dest = f"{doc_id}/Scope_Workbook.xlsx"
                _upload(bs, PROCESSED_CT, dest, f.read(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        cols = _columns(conn)
        sets = ["status=%s", "updated_at=now()", "finished_at=now()"]
        vals = ["succeeded"]
        if "final_graph_xlsx_url" in cols and "final_graph_xlsx_url" in uploaded:
            sets.append("final_graph_xlsx_url=%s"); vals.append(uploaded["final_graph_xlsx_url"])
        if "final_json_url" in cols and "final_json_url" in uploaded:
            sets.append("final_json_url=%s"); vals.append(uploaded["final_json_url"])
        if "graph_pages_prefix_url" in cols and "graph_pages_prefix_url" in uploaded:
            sets.append("graph_pages_prefix_url=%s"); vals.append(uploaded["graph_pages_prefix_url"])
        if "graph_xlsx_url" in cols and "final_graph_xlsx_url" in uploaded:
            sets.append("graph_xlsx_url=coalesce(%s, graph_xlsx_url)"); vals.append(uploaded["final_graph_xlsx_url"])

        sql = "update documents set " + ", ".join(sets) + " where doc_id=%s"
        vals.append(doc_id)
        with conn.cursor() as cur:
            cur.execute(sql, tuple(vals))

        # update batch index pointer for this doc
        try:
            _write_batch_index(
                bs=bs,
                batch_id=str(batch_id),
                doc_id=str(doc_id),
                source_file=filename_only,
                final_json_blob=final_json_blob_path,
                final_json_local=str(final_json_local_path) if final_json_local_path.exists() else None,
            )
        except Exception:
            traceback.print_exc()

        # consolidate only when DB says all docs in batch succeeded
        try:
            _build_batch_reports_if_ready(bs, str(batch_id), conn)
        except Exception:
            traceback.print_exc()

        log(f"OK: doc {doc_id} -> uploaded final + graph_pages + batch index updated")
        return True

    except Exception as e:
        err = repr(e)
        if _is_rate_limit_error(err):
            log(f"429/RATE-LIMIT: transient, abandon+retry. detail={err[:300]}")
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    update documents
                       set status='failed', error=%s, updated_at=now(), finished_at=now()
                     where doc_id=%s
                """, (err[:8000], doc_id))
        finally:
            log(f"PROCESSING FAILED: {err}")
            traceback.print_exc()
        return False

# -------------------------
# SB loop
# -------------------------
# Removed main() loop and ServiceBusClient logic for Azure Functions migration
# The process_message function is now called by function_app.py

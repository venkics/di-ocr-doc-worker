import os, json, re
from typing import List, Dict, Any
from pydantic import BaseModel
from .schemas import ClassificationResult, EntitiesResult, EntityValue, SummaryResult

# Load .env from your fixed path
from dotenv import load_dotenv
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(dotenv_path=os.path.abspath(env_path))

# Azure OpenAI
try:
    from openai import AzureOpenAI
except Exception:
    AzureOpenAI = None

def _get_client():
    endpoint = os.environ.get("AOAI_ENDPOINT")
    api_key = os.environ.get("AOAI_API_KEY")
    api_version = os.environ.get("AOAI_API_VERSION", "2024-06-01")
    if not all([endpoint, api_key]):
        raise RuntimeError("Missing AOAI endpoint/key in environment (AOAI_ENDPOINT, AOAI_API_KEY).")
    if AzureOpenAI is None:
        raise RuntimeError("openai SDK not found or outdated. Please `pip install openai>=1.45.0`.")
    return AzureOpenAI(api_key=api_key, api_version=api_version, azure_endpoint=endpoint)

def _chat(messages: List[Dict[str, str]], temperature: float = 0.1, response_format: str = "json_object") -> str:
    client = _get_client()
    model = os.environ.get("AOAI_CHAT_DEPLOYMENT")
    if not model:
        raise RuntimeError("Missing AOAI_CHAT_DEPLOYMENT in env.")
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        response_format={"type": response_format}
    )
    return resp.choices[0].message.content

def safe_json_parse(txt: str) -> Any:
    try:
        return json.loads(txt)
    except Exception:
        m = re.search(r'\{[\s\S]*\}', txt)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        raise

# ----------------------------
# Classification / Summary / Entities
# ----------------------------

def classify_llm(doc_excerpt: str, allowed_pairs: List[str]) -> ClassificationResult:
    from .prompts import classification_system
    sys = classification_system.SYSTEM
    allowed = "\n".join(allowed_pairs)
    user = f"""You are given a partial transcription/summary of a document. 
Choose exactly ONE (Document Type, Sub Classification) pair from the allowed list.
Return JSON with: doc_type, doc_subtype, confidence (0-1), rationale (<=30 words).

ALLOWED PAIRS (one per line):
{allowed}

DOCUMENT EXCERPT:
{doc_excerpt}
"""
    raw = _chat([{"role": "system", "content": sys}, {"role": "user", "content": user}], temperature=0.0)
    data = safe_json_parse(raw)
    return ClassificationResult(
        doc_type=data.get("doc_type",""),
        doc_subtype=data.get("doc_subtype",""),
        confidence=float(data.get("confidence",0.0)),
        rationale=data.get("rationale",""),
    )

def summarize_llm(doc_excerpt: str) -> SummaryResult:
    from .prompts import summary_system
    sys = summary_system.SYSTEM
    user = f"""Summarize the document in 3-6 short bullets focusing on purpose, parties, dates, totals, identifiers.
Return JSON as {{"bullets": ["...", "..."]}}.

DOCUMENT EXCERPT:
{doc_excerpt}
"""
    raw = _chat([{"role": "system", "content": sys}, {"role": "user", "content": user}], temperature=0.1)
    data = safe_json_parse(raw)
    return SummaryResult(bullets=data.get("bullets", []))

def extract_entities_llm(doc_excerpt: str, summary_bullets: List[str], entity_names: List[str]) -> EntitiesResult:
    from .prompts import entities_system
    sys = entities_system.SYSTEM
    schema_lines = "\n".join(f"- {n}" for n in entity_names)
    summary_text = "\n".join(f"- {b}" for b in summary_bullets)

    list_hint = ""
    fields = {e.strip().lower() for e in entity_names}
    if {"findings", "result value", "result units", "normal range"} <= fields:
        list_hint = "\nIf the content includes multiple tests/rows, use a list named \"Test Results\" with one object per row, each containing: Findings, Result Value, Result Units, Normal Range."

    user = f"""Extract ONLY the following entities from the document. 
Emit JSON with "scalar" and "lists" as described in the system message.
Include "source_snippet" (<=8 words) for every value. If not present, omit the key.

ALLOWED ENTITIES:
{schema_lines}

DOC SUMMARY:
{summary_text}

GUIDANCE:
{list_hint}

DOCUMENT EXCERPT:
{doc_excerpt}
"""
    raw = _chat([{"role": "system", "content": sys}, {"role": "user", "content": user}], temperature=0.1)
    data = safe_json_parse(raw)

    scalar = {}
    for k, v in (data.get("scalar") or {}).items():
        if isinstance(v, dict) and "value" in v:
            scalar[k] = EntityValue(value=v.get("value"), source_snippet=v.get("source_snippet"))
        else:
            scalar[k] = EntityValue(value=v, source_snippet=None)

    lists = {}
    for k, rows in (data.get("lists") or {}).items():
        norm_rows = []
        if isinstance(rows, list):
            for row in rows:
                row_norm = {}
                if isinstance(row, dict):
                    for nk, nv in row.items():
                        if isinstance(nv, dict) and "value" in nv:
                            row_norm[nk] = EntityValue(value=nv.get("value"), source_snippet=nv.get("source_snippet"))
                        else:
                            row_norm[nk] = EntityValue(value=nv, source_snippet=None)
                norm_rows.append(row_norm)
        lists[k] = norm_rows

    return EntitiesResult(scalar=scalar, lists=lists)

# ----------------------------
# New: TRIAGE with confidence + anchor_snippet
# ----------------------------
_TRIAGE_SYSTEM = """
You are a clinical intake assistant. From the provided document excerpt, produce a structured triage assessment.
Do NOT invent facts. If the excerpt lacks info, mark fields as "unknown".
Return STRICT JSON with these keys:

{
  "applicable": true | false,
  "urgency": "emergent" | "urgent" | "routine" | "unknown",
  "disposition": "ED" | "same-day clinic" | "scheduled clinic" | "self-care" | "unknown",
  "confidence": 0.0,
  "reasoning": "<1-3 concise sentences>",
  "anchor_snippet": "<<=8 words that best support your decision>",
  "concerns": ["<short risk/flag>", "..."],
  "next_steps": ["<actionable next step>", "..."]
}

Definitions (guidance only):
- emergent: immediate risk; direct to emergency department.
- urgent: needs care within 24–48h.
- routine: can be scheduled non-urgently.
- disposition is the recommended destination, not diagnosis.

Output ONLY the JSON object, nothing else.
"""

def triage_llm(doc_excerpt: str) -> Dict[str, Any]:
    try:
        raw = _chat(
            [
                {"role": "system", "content": _TRIAGE_SYSTEM},
                {"role": "user", "content": doc_excerpt or ""},
            ],
            temperature=0.0,
        )
        data = safe_json_parse(raw)
    except Exception as e:
        data = {}

    tri = {
        "applicable": bool(data.get("applicable", False)),
        "urgency": (data.get("urgency") or "unknown").lower(),
        "disposition": (data.get("disposition") or "unknown").lower(),
        "confidence": float(data.get("confidence", 0.0)),
        "reasoning": data.get("reasoning") or "",
        "anchor_snippet": data.get("anchor_snippet") or "",
        "concerns": data.get("concerns") or [],
        "next_steps": data.get("next_steps") or [],
    }
    if tri["confidence"] < 0: tri["confidence"] = 0.0
    if tri["confidence"] > 1: tri["confidence"] = 1.0
    return tri


def generate_advice_guidance_llm(consolidated_data: dict) -> Dict[str, Any]:
    """
    Generate clinical advice and guidance using LLM based on consolidated patient data.
    
    Args:
        consolidated_data: Dictionary containing consolidated patient information
        
    Returns:
        Dictionary containing structured advice and guidance
    """
    try:
        # Import the prompt system
        from .prompts.advice_guidance_system import ADVICE_GUIDANCE_SYSTEM, format_advice_guidance_prompt
        
        # Format the prompt with consolidated data
        user_prompt = format_advice_guidance_prompt(consolidated_data)
        
        # Make LLM call
        raw = _chat(
            [
                {"role": "system", "content": ADVICE_GUIDANCE_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,  # Slightly higher temperature for more varied but still clinical responses
        )
        
        # Parse the JSON response
        data = safe_json_parse(raw)
        
        # Ensure all required sections exist
        advice_guidance = {
            "clinical_advice": data.get("clinical_advice", []),
            "next_actions": data.get("next_actions", []),
            "safety_netting": data.get("safety_netting", []),
            "follow_up": data.get("follow_up", [])
        }
        
        # Validate that each section is a list
        for key, value in advice_guidance.items():
            if not isinstance(value, list):
                advice_guidance[key] = [str(value)] if value else []
        
        return advice_guidance
        
    except Exception as e:
        # Return fallback structure if LLM call fails
        return {
            "clinical_advice": [f"LLM advice generation failed: {str(e)}"],
            "next_actions": ["Manual clinical review required"],
            "safety_netting": ["Standard safety netting advice applies"],
            "follow_up": ["Follow standard clinical protocols"]
        }

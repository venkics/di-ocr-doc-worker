from __future__ import annotations
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any, Tuple

class FileJob(BaseModel):
    output_dir: Optional[str] = None
    final_json: Optional[str] = None
    input_path: str
    output_dir: Optional[str] = None
    layout_json: Optional[str] = None
    layout_graph_xlsx: Optional[str] = None
    document_json: Optional[str] = None
    final_json: Optional[str] = None
    pages_images: Optional[List[str]] = None  # filled by UI layer

class ClassificationResult(BaseModel):
    doc_type: str = ""
    doc_subtype: str = ""
    confidence: float = 0.0
    rationale: str = ""

class EntityValue(BaseModel):
    value: Any
    source_snippet: Optional[str] = None
    page: Optional[int] = None
    bbox: Optional[List[float]] = None  # [x0, y0, x1, y1] in image pixels

class EntitiesResult(BaseModel):
    scalar: Dict[str, EntityValue] = Field(default_factory=dict)
    lists: Dict[str, List[Dict[str, EntityValue]]] = Field(default_factory=dict)

class SummaryResult(BaseModel):
    bullets: List[str] = Field(default_factory=list)

class ScopeConfig(BaseModel):
    classes: List[Tuple[str, str]] = Field(default_factory=list)  # (Document Type, Sub Classification)
    entities_by_type: Dict[str, List[str]] = Field(default_factory=dict)  # doc_type -> [Entity Name]

class JobResult(BaseModel):
    file: str
    classification: ClassificationResult
    summary: SummaryResult
    entities: EntitiesResult

class PipelineState(BaseModel):
    jobs: List[FileJob] = Field(default_factory=list)
    scope: ScopeConfig = Field(default_factory=ScopeConfig)
    results: Dict[str, JobResult] = Field(default_factory=dict)
    di_script: str = ""  # path to llm_doc_pipeline/orchestrator/ocr/di_rest_quicktest.py
    scope_xlsx: str = ""

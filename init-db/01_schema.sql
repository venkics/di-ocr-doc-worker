-- PostgreSQL Schema for DI-OCR and Doc-Worker
-- Database: docpipeline
-- 
-- This schema is auto-created by di-ocr on startup (ensure_schema function),
-- but can also be run manually for initialization.

-- Batches table: tracks document processing batches
CREATE TABLE IF NOT EXISTS public.batches (
  batch_id   UUID PRIMARY KEY,
  patient_id TEXT,
  status     TEXT NOT NULL DEFAULT 'queued',
  created_at TIMESTAMPTZ DEFAULT now(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ
);

-- Documents table: tracks individual documents within batches
CREATE TABLE IF NOT EXISTS public.documents (
  doc_id     UUID PRIMARY KEY,
  batch_id   UUID REFERENCES public.batches(batch_id) ON DELETE CASCADE,
  blob_name  TEXT,
  blob_url   TEXT,
  filename   TEXT,
  status     TEXT DEFAULT 'queued',
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  error      TEXT,
  final_json_url TEXT,
  final_graph_xlsx_url TEXT,
  graph_pages_prefix_url TEXT,
  graph_xlsx_url TEXT
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_documents_batch_id ON public.documents(batch_id);
CREATE INDEX IF NOT EXISTS idx_documents_status ON public.documents(status);
CREATE INDEX IF NOT EXISTS idx_batches_status ON public.batches(status);

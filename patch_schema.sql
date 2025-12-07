-- Patch schema with missing columns
ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS blob_name  TEXT;
ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS blob_url   TEXT;
ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS filename   TEXT;
ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS status     TEXT;
ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();
ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ;
ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS error      TEXT;
ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS final_json_url TEXT;
ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS final_graph_xlsx_url TEXT;
ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS graph_pages_prefix_url TEXT;
ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS graph_xlsx_url TEXT;

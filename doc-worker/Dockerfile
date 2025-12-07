FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgl1 && rm -rf /var/lib/apt/lists/*

# Copy files from current directory (building from within doc-worker)
COPY requirements.txt /app/requirements.txt
RUN pip install --default-timeout=1000 --no-cache-dir -r /app/requirements.txt

COPY worker.py /app/worker.py
COPY llm_doc_pipeline /app/llm_doc_pipeline

# Copy Scope_Workbook.xlsx if it exists
RUN mkdir -p /app/assets
COPY Scope_Workbook.xlsx /app/assets/Scope_Workbook.xlsx

ENV PYTHONPATH=/app
CMD ["python","-u","/app/worker.py"]

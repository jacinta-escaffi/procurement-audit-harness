# Part B: the independent harness, packaged for Render (or any Docker host).
FROM python:3.12-slim

# pdftotext (poppler) is needed by the extract-rules skill, the findings-analyst and the quote checks.
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The whole project: CLAUDE.md, .claude/ (agents + skills), data/, scripts/, Part A outputs/ (for replay), harness/.
COPY . .

ENV PYTHONUNBUFFERED=1 \
    HARNESS_RUNS_DIR=/app/runs \
    PORT=10000
EXPOSE 10000

# Render injects $PORT; one worker keeps all runs in one process (runs execute in background threads).
CMD ["sh", "-c", "uvicorn harness.app:app --host 0.0.0.0 --port ${PORT} --workers 1"]

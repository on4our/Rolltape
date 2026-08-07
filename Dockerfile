# Rolltape on any container host — Railway, Render, Cloud Run, or a plain VM.
#
# This is the deployment the app was actually designed for: one long-lived process with a
# real filesystem. None of the serverless scaffolding (bundle trimming, /tmp redirects,
# object storage) is needed here.
FROM python:3.11-slim

# ffmpeg does the encoding. Being able to apt-get it is the entire reason a container host
# is simpler than serverless for this app.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# gunicorn is a deployment concern, not an application one, so it stays out of
# requirements.txt — CLAUDE.md pins that list deliberately.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

# Defaults suit a host with no volume attached. Mount one and point these at it to keep
# renders and the price cache across restarts.
ENV ROLLTAPE_OUT_DIR=/data/outputs \
    ROLLTAPE_CACHE_DIR=/data/.cache \
    MPLCONFIGDIR=/tmp/matplotlib \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data/outputs /data/.cache /tmp/matplotlib

EXPOSE 5000

# --workers 1 is load-bearing, not a tuning choice. Job state lives in one process's
# memory (jobs.py) and RENDER_LOCK serialises matplotlib, whose pyplot state is global.
# A second worker would give you a second job registry and renders that vanish from the
# UI — the exact failure serverless produces. Threads handle the progress polling.
CMD gunicorn --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:${PORT:-5000} app:app

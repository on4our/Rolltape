# Rolltape on any container host — Railway, Render, Cloud Run, or a plain VM.
#
# This is the deployment the app is designed for, and the only one it supports: one
# long-lived process with a real filesystem.
FROM python:3.11-slim

# ffmpeg does the encoding. Inter and JetBrains Mono are the first choices in the
# renderer's font stacks — without them every render falls back to DejaVu, which doesn't
# match the broadcast look the themes were designed around.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-inter fonts-jetbrains-mono \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# gunicorn is a deployment concern, not an application one, so it stays out of
# requirements.txt — CLAUDE.md pins that list deliberately.
#
# imageio-ffmpeg arrives via requirements.txt as the fallback for machines with no ffmpeg.
# This image apt-gets the real thing above, so dropping it in the same layer saves 77MB
# the renderer would never reach for.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn \
    && pip uninstall -y imageio-ffmpeg

COPY . .

# Defaults suit a host with no volume attached. Mount one and point these at it to keep
# renders and the price cache across restarts.
#
# Not set here, deliberately: ROLLTAPE_TWELVEDATA_KEY is a secret and belongs in the host's
# secret store rather than an image layer. A deploy that serves anyone but its owner wants
# ROLLTAPE_LICENSED_ONLY=1 alongside it — see the README.
ENV ROLLTAPE_OUT_DIR=/data/outputs \
    ROLLTAPE_CACHE_DIR=/data/.cache \
    MPLCONFIGDIR=/tmp/matplotlib \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data/outputs /data/.cache /tmp/matplotlib

# Index the fonts at build time so the first render doesn't pay for the font scan.
RUN python -c "import matplotlib.font_manager"

EXPOSE 5000

# --workers 1 is load-bearing, not a tuning choice: job state lives in one process's
# memory (jobs.py), so a second worker gives you a second registry and renders that start,
# finish, and never appear in the UI that asked for them. Threads handle the progress
# polling. Renders themselves are already out of this process — see render_job.py — so
# there is nothing to gain by adding workers anyway.
CMD gunicorn --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:${PORT:-5000} app:app

"""Rolltape - ticker in, animated chart video out."""

import argparse
import base64
import io
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from queue import Queue

import matplotlib.pyplot as plt
from flask import Flask, jsonify, request, send_from_directory

import data as datasrc
import renderers

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "outputs")
os.makedirs(OUT_DIR, exist_ok=True)

app = Flask(__name__, static_folder=None, template_folder="templates")

JOBS = OrderedDict()
QUEUE = Queue()
RENDER_LOCK = threading.Lock()  # matplotlib state is global; one draw at a time


# ---------------------------------------------------------------------------
# Config normalising
# ---------------------------------------------------------------------------
def clean_config(raw):
    chart = raw.get("chart", "line")
    spec = renderers.CHARTS.get(chart)
    if not spec:
        raise ValueError(f"Unknown chart type: {chart}")

    tickers = [t.strip().upper() for t in raw.get("tickers", []) if str(t).strip()]
    needs_ticker = not (chart == "bars" and raw.get("rows"))
    if needs_ticker and not tickers:
        raise ValueError("Add at least one ticker.")
    tickers = tickers[: spec["tickers"]]
    if chart in ("compare", "race") and len(tickers) < 2:
        raise ValueError("This chart needs at least two tickers.")

    cfg = {
        "chart": chart,
        "tickers": tickers,
        "start": raw.get("start") or "2024-01-01",
        "end": raw.get("end") or None,
        "duration": max(float(raw.get("duration", 6)), 0.5),
        "hold": max(float(raw.get("hold", 1.5)), 0.0),
        "easing": raw.get("easing", "out"),
        "theme": raw.get("theme", "midnight"),
        "aspect": raw.get("aspect", "16:9"),
        "quality": raw.get("quality", "final"),
        "title": (raw.get("title") or "").strip() or None,
        "subtitle": (raw.get("subtitle") or "").strip() or None,
        "footer": (raw.get("footer") or "").strip() or None,
        "normalize": bool(raw.get("normalize", True)),
        "max_candles": int(raw.get("max_candles", 90) or 90),
        "metric": raw.get("metric", "return"),
        "rows": raw.get("rows") or [],
        "annotations": raw.get("annotations") or [],
        "unit": raw.get("unit", ""),
        "decimals": int(raw.get("decimals", 1) or 1),
    }
    return cfg


def slug(cfg):
    base = "-".join(cfg["tickers"][:3]) or cfg["chart"]
    name = f"{cfg['chart']}_{base}_{time.strftime('%m%d-%H%M%S')}"
    return re.sub(r"[^A-Za-z0-9_.-]", "", name) + ".mp4"


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------
def worker():
    while True:
        job_id = QUEUE.get()
        job = JOBS.get(job_id)
        if not job or job["status"] == "cancelled":
            QUEUE.task_done()
            continue
        job["status"] = "rendering"
        job["started"] = time.time()
        try:
            path = os.path.join(OUT_DIR, job["file"])

            def progress(i, n):
                job["progress"] = i
                job["total"] = n

            with RENDER_LOCK:
                renderers.render(job["cfg"], path, progress=progress)
            job["status"] = "done"
            job["seconds"] = round(time.time() - job["started"], 1)
            job["size_mb"] = round(os.path.getsize(path) / 1e6, 2)
        except Exception as exc:  # noqa: BLE001
            job["status"] = "failed"
            job["error"] = str(exc)
        finally:
            QUEUE.task_done()


threading.Thread(target=worker, daemon=True).start()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return send_from_directory(app.template_folder, "index.html")


@app.get("/api/meta")
def meta():
    return jsonify({
        "charts": [{"id": k, "label": v["label"], "desc": v["desc"],
                    "max_tickers": v["tickers"]}
                   for k, v in renderers.CHARTS.items()],
        "themes": [{"id": k, "label": v["label"], "bg": v["bg"],
                    "swatch": [v["up"], v["down"], *v["series"][:3]]}
                   for k, v in renderers.THEMES.items()],
        "sizes": {a: {q: list(s) for q, s in qs.items()}
                  for a, qs in renderers.SIZES.items()},
        "fps": {k: v["fps"] for k, v in renderers.ENCODE.items()},
        "demo": datasrc.is_demo(),
    })


@app.post("/api/preview")
def preview():
    try:
        cfg = clean_config(request.get_json(force=True))
        at = float(request.args.get("at", 0.75))
        with RENDER_LOCK:
            fig = renderers.still_frame(cfg, at=at)
            buf = io.BytesIO()
            fig.savefig(buf, format="png",
                        facecolor=renderers.THEMES[cfg["theme"]]["bg"], dpi=90)
            plt.close(fig)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return jsonify({"image": f"data:image/png;base64,{b64}"})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 400


@app.post("/api/render")
def start_render():
    try:
        cfg = clean_config(request.get_json(force=True))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 400

    job_id = uuid.uuid4().hex[:10]
    fps = renderers.ENCODE[cfg["quality"]]["fps"]
    JOBS[job_id] = {
        "id": job_id,
        "cfg": cfg,
        "file": slug(cfg),
        "label": cfg["title"] or " ".join(cfg["tickers"]) or cfg["chart"],
        "chart": renderers.CHARTS[cfg["chart"]]["label"],
        "status": "queued",
        "progress": 0,
        "total": int((cfg["duration"] + cfg["hold"]) * fps),
        "created": time.time(),
        "error": None,
    }
    QUEUE.put(job_id)
    return jsonify({"id": job_id})


@app.get("/api/jobs")
def list_jobs():
    out = []
    for j in list(JOBS.values())[-25:][::-1]:
        out.append({k: v for k, v in j.items() if k != "cfg"})
    return jsonify(out)


@app.post("/api/jobs/<job_id>/cancel")
def cancel(job_id):
    job = JOBS.get(job_id)
    if job and job["status"] == "queued":
        job["status"] = "cancelled"
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 400


@app.get("/outputs/<path:name>")
def outputs(name):
    return send_from_directory(OUT_DIR, name)


@app.post("/api/cache/clear")
def clear_cache():
    datasrc.clear_cache()
    return jsonify({"ok": True})


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--demo", action="store_true",
                   help="Use generated data instead of Yahoo (for offline testing)")
    a = p.parse_args()
    datasrc.set_demo(a.demo)
    print(f"\n  Rolltape running at http://{a.host}:{a.port}"
          f"{'  [demo data]' if a.demo else ''}\n")
    app.run(host=a.host, port=a.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()

"""Rolltape - ticker in, animated chart video out."""

import argparse
import base64
import io
import os
import re
import threading
import time
import uuid
from datetime import date
from queue import Queue

from flask import Flask, Response, jsonify, request, send_from_directory

import config
import data as datasrc
import jobs as jobstore
import render_job
import renderers
import storage

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = config.OUT_DIR

app = Flask(__name__, static_folder=None, template_folder="templates")

QUEUE = Queue()

# Guards the drawing this process does itself — previews and stills — because pyplot state
# is global. Renders are not on it: they run in their own process (render_job.py), which is
# what stops a seventy-second render from freezing every preview behind it.
DRAW_LOCK = threading.Lock()

# The preview is drawn at draft dimensions and then base64'd into a JSON response, so it
# trades a little sharpness for a smaller payload. The still export renders at the real
# frame size instead — see /api/still.
PREVIEW_DPI = 90

datasrc.set_demo(config.DEMO)


# ---------------------------------------------------------------------------
# Config normalising
# ---------------------------------------------------------------------------
def _choice(value, options, default, label):
    """Pick one of a fixed set of numbers, falling back to the tier default when unset."""
    if value in (None, "", 0):
        return default
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = None
    if value not in options:
        allowed = ", ".join(str(o) for o in options)
        raise ValueError(f"{label} must be one of: {allowed}.")
    return value


def _clamp_start(start, interval):
    """Pull a start date forward to the furthest back this interval can reach.

    Yahoo keeps only a window of intraday history — a week of minute bars, two months of
    five-minute. Asking for more returns silence rather than an error. Rejecting the
    request would be the strict reading, but the start date defaults to a year that no
    intraday interval can serve, so every first switch to 5m would be an error message
    instead of a chart. Clamping renders the window that exists, and the subtitle names
    the range it actually drew.
    """
    days = datasrc.max_lookback_days(interval)
    if not days:
        return start
    floor = (time.time() - days * 86400)
    try:
        asked = time.mktime(time.strptime(start, "%Y-%m-%d"))
    except (ValueError, TypeError):
        return time.strftime("%Y-%m-%d", time.localtime(floor))
    return time.strftime("%Y-%m-%d", time.localtime(max(asked, floor)))


CUSTOM_RANGE = "custom"
DEFAULT_START = "2024-01-01"


def _date(value, label):
    """An ISO date, or None. The date inputs only ever send this shape; the API might not."""
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(f"{label} must look like 2024-01-31.") from None


def resolve_window(raw):
    """Turn the date selector into a concrete fetch window.

    A preset resolves here rather than in the browser, so "year to date" posted to the API
    still means year to date next January, and so the renderers keep seeing nothing but
    plain start and end dates. A preset names its own interval; a custom range takes
    whichever one was posted alongside the dates.
    """
    name = raw.get("range") or CUSTOM_RANGE
    if name != CUSTOM_RANGE:
        return {"range": name, **datasrc.resolve_range(name)}

    start = _date(raw.get("start"), "Start date") or DEFAULT_START
    end = _date(raw.get("end"), "End date")
    if end and end <= start:
        raise ValueError("The end date has to be after the start date.")
    return {"range": CUSTOM_RANGE, "start": start, "end": end,
            "interval": raw.get("interval") or datasrc.DEFAULT_INTERVAL, "sessions": None}


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

    # The quality tier sets crf and preset, and seeds frame rate and resolution. Either of
    # those two can be overridden on its own — the slate in the UI edits them directly —
    # so resolve them to concrete numbers here and let the renderers stop caring which
    # tier they came from.
    quality = raw.get("quality", "final")
    enc = renderers.ENCODE.get(quality) or renderers.ENCODE["final"]
    fps = _choice(raw.get("fps"), renderers.FPS_CHOICES, enc["fps"], "Frame rate")
    resolution = _choice(raw.get("resolution"), renderers.RESOLUTIONS, enc["res"],
                         "Resolution")

    # The window decides the interval — a preset carries its own, a custom range is told
    # one — so it has to resolve before the interval can be checked.
    window = resolve_window(raw)
    interval = window["interval"]
    if interval not in datasrc.INTERVALS:
        raise ValueError(f"Unknown interval: {interval}")
    if datasrc.is_intraday(interval) and not datasrc.intraday_available():
        raise ValueError(
            "Intraday needs yfinance, which isn't installed. "
            "Use daily bars, or pip install -r requirements.txt.")

    cfg = {
        "chart": chart,
        "tickers": tickers,
        "range": window["range"],
        # Clamped last: a preset can name a start further back than its interval reaches
        # (10Y at 5m), and Yahoo answers that with silence rather than an error.
        "start": _clamp_start(window["start"], interval),
        "end": window["end"],
        "interval": interval,
        "sessions": window["sessions"],
        "duration": max(float(raw.get("duration", 6)), 0.5),
        "hold": max(float(raw.get("hold", 1.5)), 0.0),
        "easing": raw.get("easing", "out"),
        "theme": raw.get("theme", "midnight"),
        "aspect": raw.get("aspect", "16:9"),
        "quality": quality,
        "fps": fps,
        "resolution": resolution,
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
        "transparent": bool(raw.get("transparent", False)),
    }
    return cfg


def slug(cfg, ext=None):
    base = "-".join(cfg["tickers"][:3]) or cfg["chart"]
    name = f"{cfg['chart']}_{base}_{time.strftime('%m%d-%H%M%S')}"
    # The container follows the codec, and the codec follows the alpha setting — ask
    # renderers rather than deciding it twice.
    ext = ext or renderers.output_extension(cfg["transparent"])
    return re.sub(r"[^A-Za-z0-9_.-]", "", name) + ext


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------
def worker():
    while True:
        job_id = QUEUE.get()
        job = jobstore.get(job_id)
        if not job or job["status"] == "cancelled":
            QUEUE.task_done()
            continue
        started = time.time()
        jobstore.update(job_id, status="rendering", started=started)
        try:
            path = storage.render_target(job["file"])

            def progress(i, n):
                jobstore.update(job_id, progress=i, total=n)

            # Deliberately not under DRAW_LOCK — the render has its own process and its own
            # pyplot state, so previews carry on being answered while it runs. One worker
            # thread is still what keeps renders from piling onto the CPU together.
            render_job.run(job["cfg"], path, progress=progress,
                           demo=datasrc.is_demo())
            # Size has to be read before publish; a remote backend may move the file.
            size_mb = round(os.path.getsize(path) / 1e6, 2)
            url = storage.publish(path, job["file"])
            jobstore.update(job_id, status="done", url=url, size_mb=size_mb,
                            seconds=round(time.time() - started, 1))
        except Exception as exc:  # noqa: BLE001
            jobstore.update(job_id, status="failed", error=str(exc))
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
        # Each preset ships with the window it resolves to right now, so the interface can
        # spell out the dates without doing the arithmetic a second time and disagreeing
        # with the server about what "last year" means.
        "ranges": [{"id": k, "short": v["short"], "label": v["label"],
                    **datasrc.resolve_range(k)}
                   for k, v in datasrc.RANGES.items()],
        "sizes": {a: {str(r): list(s) for r, s in rs.items()}
                  for a, rs in renderers.SIZES.items()},
        "resolutions": list(renderers.RESOLUTIONS),
        "fps_choices": list(renderers.FPS_CHOICES),
        "tiers": {k: {"fps": v["fps"], "res": v["res"]}
                  for k, v in renderers.ENCODE.items()},
        "intervals": [{"id": k, "label": v["label"], "days": v["days"]}
                      for k, v in datasrc.INTERVALS.items()],
        "intraday": datasrc.intraday_available(),
        "demo": datasrc.is_demo(),
    })


@app.post("/api/preview")
def preview():
    try:
        cfg = clean_config(request.get_json(force=True))
        at = float(request.args.get("at", 0.75))
        buf = io.BytesIO()
        with DRAW_LOCK:
            renderers.save_still(cfg, buf, at=at, dpi=PREVIEW_DPI)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return jsonify({"image": f"data:image/png;base64,{b64}"})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 400


@app.post("/api/still")
def still():
    """One frame at full output resolution, as a downloadable PNG.

    Same config, same frame, same size as the video — which makes it the thumbnail for
    the video without a screenshot or a round trip through an editor.
    """
    try:
        cfg = clean_config(request.get_json(force=True))
        at = float(request.args.get("at", 0.75))
        buf = io.BytesIO()
        with DRAW_LOCK:
            renderers.save_still(cfg, buf, at=at, quality=cfg["quality"],
                                 res=cfg["resolution"])
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 400

    name = slug(cfg, ext=".png")
    return Response(buf.getvalue(), mimetype="image/png",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/render")
def start_render():
    try:
        cfg = clean_config(request.get_json(force=True))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 400

    job_id = uuid.uuid4().hex[:10]
    jobstore.create({
        "id": job_id,
        "cfg": cfg,
        "file": slug(cfg),
        "label": cfg["title"] or " ".join(cfg["tickers"]) or cfg["chart"],
        "chart": renderers.CHARTS[cfg["chart"]]["label"],
        "status": "queued",
        "progress": 0,
        "total": int((cfg["duration"] + cfg["hold"]) * cfg["fps"]),
        "created": time.time(),
        "url": None,
        "error": None,
    })
    QUEUE.put(job_id)
    return jsonify({"id": job_id})


@app.get("/api/jobs")
def list_jobs():
    out = []
    for j in jobstore.recent():
        out.append({k: v for k, v in j.items() if k != "cfg"})
    return jsonify(out)


@app.post("/api/jobs/<job_id>/cancel")
def cancel(job_id):
    job = jobstore.get(job_id)
    if job and job["status"] == "queued":
        jobstore.update(job_id, status="cancelled")
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
    datasrc.set_demo(a.demo or config.DEMO)
    print(f"\n  Rolltape running at http://{a.host}:{a.port}"
          f"{'  [demo data]' if a.demo else ''}\n")
    app.run(host=a.host, port=a.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()

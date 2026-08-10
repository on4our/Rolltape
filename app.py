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

from flask import (Flask, Response, jsonify, redirect, render_template, request,
                   send_file, send_from_directory, url_for)

import config
import data as datasrc
import examples as showcase
import jobs as jobstore
import render_job
import presets
import renderers
import signups
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


def _one_of(value, options, default, label):
    """Pick one of a fixed set of names, falling back when the field is absent."""
    value = (str(value).strip().lower() if value not in (None, "") else default)
    if value not in options:
        raise ValueError(f"{label} must be one of: {', '.join(options)}.")
    return value


def format_title(fmt, chart, tickers):
    """Fill a brand kit's default title.

    Only tokens knowable from the config go in here — the date range and return live in
    each chart's subtitle and aren't available until the data is fetched. Unknown tokens
    are left as written rather than raising: a typo in a saved kit should look wrong on
    the preview, not fail the render.
    """
    out = str(fmt or "").strip()
    if not out:
        return ""
    for token, value in (
        ("{ticker}", tickers[0] if tickers else ""),
        ("{tickers}", ", ".join(tickers)),
        ("{chart}", renderers.CHARTS.get(chart, {}).get("label", "")),
    ):
        out = out.replace(token, value)
    return out.strip()


def ma_periods(raw):
    """Moving-average windows, in trading days.

    Accepts a list or a comma-separated string, because the UI field is free text. Junk is
    dropped rather than raising — a typo in an optional overlay shouldn't fail the render.
    Capped at three so the key stays readable and the run-up fetch stays bounded.
    """
    if isinstance(raw, str):
        raw = raw.replace(",", " ").split()
    out = []
    for v in raw or []:
        try:
            n = int(float(str(v).strip()))
        except (TypeError, ValueError):
            continue
        if 2 <= n <= 400 and n not in out:
            out.append(n)
    return sorted(out)[:3]


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
    # tier they came from. Both are indexed directly downstream, so a bad value has to
    # fail here as a 400 rather than as a KeyError once the job is already queued.
    quality = raw.get("quality") or "final"
    if quality not in renderers.ENCODE:
        raise ValueError(f"Unknown quality: {quality}")
    enc = renderers.ENCODE[quality]
    fps = _choice(raw.get("fps"), renderers.FPS_CHOICES, enc["fps"], "Frame rate")
    resolution = _choice(raw.get("resolution"), renderers.RESOLUTIONS, enc["res"],
                         "Resolution")
    preset = str(raw.get("preset") or "auto").strip().lower()
    if preset != "auto" and preset not in renderers.PRESETS:
        raise ValueError(f"Unknown encoder preset: {preset}")

    # A typed title always wins over the brand kit's template, and a template that fills
    # in to nothing falls through to whatever default the chart itself uses.
    title = (raw.get("title") or "").strip()
    if not title:
        title = format_title(raw.get("title_format"), chart, tickers)

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
    # The camera defaults to locked, which is the framing every chart used before there
    # was a camera at all — so an existing config renders exactly as it always has, and
    # the move is something you go and ask for.
    camera = _one_of(raw.get("camera"), renderers.CAMERAS, "locked", "Camera move")
    travel = _one_of(raw.get("camera_travel"), renderers.TRAVELS, "standard",
                     "Camera travel")
    camera_y = _one_of(raw.get("camera_y"), renderers.CAMERA_Y, "track",
                       "Camera vertical")

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
        "camera": camera,
        "camera_travel": travel,
        "camera_y": camera_y,
        "theme": raw.get("theme", "midnight"),
        "aspect": raw.get("aspect", "16:9"),
        "quality": quality,
        "fps": fps,
        "resolution": resolution,
        "preset": preset,
        "title": title or None,
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
        "log_scale": bool(raw.get("log_scale", False)),
        "ma": ma_periods(raw.get("ma")),
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
            render_job.run(job["cfg"], path, progress=progress)
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
def root():
    # A local run has no use for a marketing page in front of its own tool, so the app
    # keeps "/" unless a host says otherwise. See config.LANDING.
    if config.LANDING:
        return landing()
    return index()


@app.get("/app")
def index():
    # Served as a file rather than through Jinja: index.html is 960 lines of inline JS
    # and there is nothing in it for a template engine to do. The landing page is the
    # opposite — all content, no behaviour — so that one is rendered.
    return send_from_directory(app.template_folder, "index.html")


# Served from every instance rather than only the public one, because the point of a
# pricing page is that someone can be sent a link to it. The app only *links* to it on a
# public instance — see the topbar in index.html — so a local install stays a tool rather
# than a shopfront.
@app.get("/pricing")
def pricing():
    return send_from_directory(app.template_folder, "pricing.html")


@app.get("/landing")
def landing():
    """The public page. Always reachable, so it can be checked without setting the flag."""
    return render_template(
        "landing.html",
        charts=list(renderers.CHARTS.values()),
        examples=showcase.EXAMPLES,
        themes=len(renderers.THEMES),
        # Both point at this same process by default, which is what makes the demo
        # container self-contained: the page and the thing it advertises are one deploy.
        demo_url=config.DEMO_URL,
    )


@app.get("/examples/<example_id>.png")
def example_still(example_id):
    """One showcase frame, drawn once and cached.

    A 404 here is not an error worth showing anyone — the page falls back to describing
    the chart in words. That matters because the most likely cause is the data source
    being down, which is exactly when the landing page should still load.
    """
    spec = showcase.EXAMPLES.get(example_id)
    if not spec:
        return jsonify({"error": "No such example."}), 404

    path = showcase.path_for(example_id, config.EXAMPLES_DIR)
    if not os.path.exists(path):
        try:
            cfg = clean_config(dict(spec["cfg"]))
            with DRAW_LOCK:
                showcase.write_still(example_id, cfg, config.EXAMPLES_DIR)
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("Example %s failed to draw: %s", example_id, exc)
            return jsonify({"error": "Not available."}), 404

    resp = send_file(path, mimetype="image/png")
    # The frame for a given example only changes when its config does, and a stale one is
    # a slightly old chart rather than a wrong page.
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


def _page_url(**params):
    """URL of the landing page, which sits at "/" or "/landing" depending on the flag."""
    return url_for("root" if config.LANDING else "landing", _anchor="signup", **params)


@app.post("/api/signup")
def signup():
    """Email capture. Works as a fetch and as a plain form post.

    The form fallback is not ceremony: this is the only conversion on the page, and a
    page that silently does nothing with JS blocked converts at zero.
    """
    wants_json = request.is_json
    body = request.get_json(silent=True) or request.form
    try:
        signups.add(body.get("email"), source=(body.get("source") or "landing"))
    except signups.SignupError as exc:
        if wants_json:
            return jsonify({"error": str(exc)}), 400
        return redirect(_page_url(signup="error", message=str(exc)))
    if wants_json:
        return jsonify({"ok": True})
    return redirect(_page_url(signup="ok"))


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
        "cameras": [{"id": k, "label": v["label"], "desc": v["desc"]}
                    for k, v in renderers.CAMERAS.items()],
        "travels": list(renderers.TRAVELS),
        "sizes": {a: {str(r): list(s) for r, s in rs.items()}
                  for a, rs in renderers.SIZES.items()},
        "resolutions": list(renderers.RESOLUTIONS),
        "fps_choices": list(renderers.FPS_CHOICES),
        "tiers": {k: {"fps": v["fps"], "res": v["res"]}
                  for k, v in renderers.ENCODE.items()},
        "intervals": [{"id": k, "label": v["label"], "days": v["days"]}
                      for k, v in datasrc.INTERVALS.items()],
        "intraday": datasrc.intraday_available(),
        "sizes": {a: {q: list(s) for q, s in qs.items()}
                  for a, qs in renderers.SIZES.items()},
        "presets": list(renderers.PRESETS),
        "auto_preset": {k: v["preset"] for k, v in renderers.ENCODE.items()},
        # A public instance is where someone is deciding whether to buy, so that is where
        # the interface shows a way through to the prices.
        "public": config.LANDING,
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


@app.get("/api/presets")
def list_presets():
    return jsonify(presets.all_kits())


@app.post("/api/presets")
def save_preset():
    body = request.get_json(force=True) or {}
    kit = {field: body.get(field) for field in presets.FIELDS}
    if kit["theme"] and kit["theme"] not in renderers.THEMES:
        return jsonify({"error": f"Unknown theme: {kit['theme']}"}), 400
    try:
        name, cleaned = presets.save(body.get("name"), kit)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"name": name, "kit": cleaned})


@app.delete("/api/presets/<name>")
def delete_preset(name):
    if not presets.delete(name):
        return jsonify({"error": f"No brand kit named {name}."}), 404
    return jsonify({"ok": True})


@app.post("/api/cache/clear")
def clear_cache():
    datasrc.clear_cache()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
# Werkzeug's default is an unstyled Times New Roman page, which is a jarring place for a
# visitor to land from a page that otherwise looks finished — and it is reachable in normal
# use, because a Download link stops resolving once its file has left `outputs/`.
#
# /api/* is excluded on purpose. The interface calls .json() on every API response, so an
# HTML body under that prefix would turn a missing route into a parse error on the client
# instead of a message. Routes that already answer their own 404 through jsonify —
# delete_preset, the example stills — are untouched either way: an errorhandler only fires
# for a raised or aborted response, never for one a view returned itself.
def _error_page(code, title, message):
    if request.path.startswith("/api/"):
        return jsonify({"error": message}), code
    return render_template("error.html", code=code, title=title, message=message,
                           path=request.path), code


@app.errorhandler(404)
def not_found(_exc):
    # A 404 under /outputs/ means the render itself is gone, which is worth saying plainly
    # — the file outlives a restart, so it missing means something removed it.
    if request.path.startswith("/outputs/"):
        return _error_page(
            404, "That render isn't there any more.",
            "Rendered files stay in the outputs folder between restarts, so this one was "
            "moved or deleted — or the server is running somewhere that doesn't keep the "
            "folder. Render it again and the new link will work.")
    return _error_page(
        404, "Nothing at this address.",
        "Check the address, or head back to the app.")


@app.errorhandler(500)
def server_error(_exc):
    return _error_page(
        500, "Rolltape hit an error.",
        "The terminal running the server has the traceback. Renders already finished are "
        "still in the outputs folder.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--host", default="127.0.0.1")
    a = p.parse_args()
    feed = "Twelve Data" if config.TWELVEDATA_KEY else "Yahoo/Stooq"
    print(f"\n  Rolltape running at http://{a.host}:{a.port}  [{feed}]\n")
    app.run(host=a.host, port=a.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()

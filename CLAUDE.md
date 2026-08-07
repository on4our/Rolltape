# Rolltape

Ticker in, animated chart video out. A local Flask app that renders stock chart
animations to MP4 for use in YouTube videos.

Built for a solo investing channel. Every design decision favours **output that looks
broadcast-ready straight out of the renderer** over configurability. If a feature would
need the user to fiddle in After Effects afterwards, it isn't done.

## Run it

```bash
pip install -r requirements.txt
python app.py                    # http://127.0.0.1:5000
python app.py --demo             # generated data, no network needed
python app.py --host 0.0.0.0     # reachable from phone on the same wifi
```

ffmpeg must be on PATH. Everything else is pip.

## Architecture

```
app.py          Flask routes, single-threaded render queue, config validation
renderers.py    Six chart types + themes + easing + ffmpeg export
data.py         Yahoo fetch, Stooq fallback, CSV disk cache, demo generator
config.py       Env-var configuration; every default reproduces the local setup
storage.py      Where a finished MP4 lands — local disk or object storage
jobs.py         The render job registry
templates/      One HTML file, inline CSS and JS, no build step
outputs/        Rendered MP4s
```

Deployment support sits off to the side and nothing local reads it: `Dockerfile`,
`api/index.py` (WSGI entrypoint), `vercel.json`, `requirements-vercel.txt`,
`scripts/trim-bundle.sh`.

There is deliberately no frontend build step and no database. Job state is an in-memory
`OrderedDict` in `jobs.py` that resets on restart. `config.py`, `storage.py` and
`jobs.py` exist as seams so the app can boot on a read-only filesystem — they are not an
abstraction layer to grow. Don't add a build pipeline or an ORM without a concrete reason.

## How a render works

1. Browser POSTs config JSON to `/api/render`.
2. `clean_config()` normalises and validates it. **All validation lives here** — the
   renderers assume a clean config and will raise unhelpfully otherwise.
3. Job goes on a `Queue`; one worker thread drains it.
4. `renderers.render(cfg, path, progress)` dispatches on `cfg["chart"]` via the `CHARTS`
   registry.
5. Browser polls `/api/jobs` for per-frame progress.

## Renderer contract

Every chart function has the same signature:

```python
def render_x(cfg, ctx, out, progress=None, still=None) -> str | Figure
```

- `ctx` is a `Ctx` — theme dict, pixel dimensions, fps, encoder settings. `ctx.s` is the
  font/line scale factor so a 720p draft is visually proportional to a 1080p final.
  **Multiply every font size and line width by `ctx.s`.** Forgetting this is the most
  common bug when adding a chart type.
- `still=<float 0..1>` returns the Figure at that point through the animation instead of
  encoding. This powers the live preview. Every renderer must support it — the preview is
  the main reason the tool is pleasant to use.
- `progress(i, n)` is passed straight to matplotlib's `progress_callback`.

To add a chart type: write the function, add an entry to `CHARTS` at the bottom of
`renderers.py`. The UI builds its chart list from `/api/meta`, so it appears
automatically. Chart-specific form fields need a `data-for="yourchart"` attribute in
`index.html`.

## Conventions that matter

**Themes.** All colour lives in the `THEMES` dict at the top of `renderers.py`. Never
hardcode a colour inside a renderer. Adding a theme requires no other change.

**Quality tiers.** A tier owns `crf` and `preset` outright, but only *seeds* `fps` and
`res` — the slate overrides those two independently. `clean_config()` resolves both to
concrete numbers, so `cfg["fps"]` and `cfg["resolution"]` are always set and nothing
downstream re-derives them from the tier. `SIZES` is keyed by the short side (720, 1080,
1440), and the aspect decides which side that is.

**Motion.** `ease()` maps normalised time to progress; `_plan()` maps frame index to a
position along a densely-interpolated series. Series are upsampled to ~2x the frame count
before animating so the line head moves smoothly rather than hopping between daily closes.
Any time-based motion should be frame-rate independent — see the race renderer's
`1 - exp(-11/fps)` smoothing rather than a fixed per-frame constant.

**Mobile CSS.** The narrow-screen media query sits at the *end* of the stylesheet and must
stay there. It has the same specificity as the base rules, so moving it earlier silently
breaks the mobile layout. Inputs are 16px on mobile because Safari zooms the page for
anything smaller.

**Threading.** matplotlib's pyplot state is global. `RENDER_LOCK` serialises previews and
renders. Don't parallelise renders in-process — use separate processes if throughput ever
matters.

## Known rough edges

- `preset=slow` on the `max` quality tier is genuinely slow — roughly 70s for a 7.5s
  1080p60 clip — and needs enough memory that small container hosts may OOM-kill ffmpeg.
  `final` uses `medium` for this reason. Worth exposing the preset in the UI.
- Daily bars only. Intraday needs `interval="5m"` and is limited to ~60 days of history.
  Note that Stooq's CSV endpoint is daily-grain, so intraday would have no fallback — it
  is the one feature that breaks outright when Yahoo does.
- Bar race row ordering can look unsettled if a rank flips in the final frames. Longer
  hold masks it.
- Tests cover `data.py` only. `clean_config()` holds all validation and has none.
- A serverless deploy can't finish a render. `/api/render` returns as soon as the job is
  queued and the work happens on a daemon thread, but the instance is frozen once the
  response is sent — so the render may never run, not merely go missing from the
  registry. A container host is the shape this app was written for; see README.

## Roadmap

Near term, in rough priority order:

1. Intraday interval option — needed for same-day coverage of Fed days and earnings gaps.
2. Encoder preset exposed in the UI (`final` now defaults to `medium`).
3. Brand kit: save theme, footer, and default title format as a named preset.
4. Batch render — one config, many tickers, queued.

Done: Stooq fallback in `data.py`, so a render survives Yahoo changing its endpoints.

Further out, this is being explored as a product. That means watermarking on a free tier,
render credits, and eventually an API endpoint that accepts a config and returns an MP4.
**Before any of that ships, the data source must be replaced with a licensed feed** —
yfinance scrapes Yahoo and redistributing that data to paying users is not permitted.
Tiingo, Twelve Data, EOD Historical and Polygon all license end-of-day US equities in the
$30-100/month range.

## Style

Python: standard library plus the four deps, no frameworks beyond Flask. Comments explain
*why*, not *what* — the existing comments are the reference for tone. Keep functions flat
and readable over clever.

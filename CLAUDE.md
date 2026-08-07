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
app.py          Flask routes, single-threaded render queue, job state
renderers.py    Six chart types + themes + easing + ffmpeg export
data.py         Yahoo fetch (yfinance) with CSV disk cache, plus demo generator
presets.py      Named brand kits, saved to one JSON file
templates/      One HTML file, inline CSS and JS, no build step
outputs/        Rendered MP4s
```

There is deliberately no frontend build step and no database. Job state is an in-memory
`OrderedDict` that resets on restart, which is fine — a job outlives its render by
minutes. Brand kits are the exception and the only state meant to survive: `presets.py`
keeps them in a single JSON document, written atomically, read fresh on every call. That
is still not a database, and a build pipeline or an ORM still needs a concrete reason.

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

**Motion.** `ease()` maps normalised time to progress; `_plan()` maps frame index to a
position along a densely-interpolated series. Series are upsampled to ~2x the frame count
before animating so the line head moves smoothly rather than hopping between daily closes.
Any time-based motion should be frame-rate independent — see the race renderer's
`1 - exp(-11/fps)` smoothing rather than a fixed per-frame constant.

**Mobile CSS.** The narrow-screen media query sits at the *end* of the stylesheet and must
stay there. It has the same specificity as the base rules, so moving it earlier silently
breaks the mobile layout. Inputs are 16px on mobile because Safari zooms the page for
anything smaller.

**Brand kits.** A kit is theme, footer and a default title template. The client applies a
kit to its own form state and sends the resolved values, so the server never needs to know
which kit is active — `presets.py` is just where they are kept. The one exception is the
title template: `format_title()` fills it in `clean_config()`, so the preview and the
render agree and an API caller gets it too. Its tokens are deliberately limited to what
the config knows (`{ticker}`, `{tickers}`, `{chart}`) — the date range and return live in
each chart's subtitle and aren't known until the data is fetched.

**Threading.** matplotlib's pyplot state is global. `RENDER_LOCK` serialises previews and
renders. Don't parallelise renders in-process — use separate processes if throughput ever
matters.

## Known rough edges

- **Render time is matplotlib, not ffmpeg.** A 7.5s 1080p60 clip takes ~90s, and drawing
  the 450 frames is nearly all of it. The x264 preset spans only ~3s of that (veryfast
  3.5s, medium 4.3s, slow 6.5s), and medium and slow produce the same file size. Anything
  that meaningfully speeds up a render has to make `_new_fig`/draw cheaper — blitting,
  reusing artists between frames, or rendering in parallel processes. Chasing the encoder
  settings is not worth it; that was measured, not assumed.
- Daily bars only. Intraday needs `interval="5m"` and is limited to ~60 days of history.
  Note that Stooq's CSV endpoint is daily-only, so intraday would have no fallback source.
- Bar race row ordering can look unsettled if a rank flips in the final frames. Longer
  hold masks it.

## Roadmap

Near term, in rough priority order:

1. Batch render — one config, many tickers, queued. The queue, worker, progress polling
   and cancel already handle N jobs, so this is mostly a loop over `jobstore.create`.
2. Frame-drawing speed — the only lever that actually shortens a render. See the first
   known rough edge for where the time really goes.

Done: the Stooq fallback; the encoder preset (`final` moved from `slow` to `medium`, with
an Auto/Faster/Slower override in the UI); and brand kits. Intraday is deliberately not
planned.

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

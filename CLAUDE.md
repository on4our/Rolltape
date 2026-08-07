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
templates/      One HTML file, inline CSS and JS, no build step
outputs/        Rendered MP4s
```

There is deliberately no frontend build step and no database. Job state is an in-memory
`OrderedDict` that resets on restart. Don't add a build pipeline or an ORM without a
concrete reason.

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
- **Anything drawn *behind* the chart must use `ctx.bg`, never `theme["bg"]`.** `ctx.bg`
  is `"none"` on a transparent export, so a renderer that reaches for the theme colour
  directly paints an opaque backdrop into what was supposed to be an overlay. Same class
  of bug as forgetting `ctx.s`, and just as invisible until someone drops the clip on a
  timeline. The one deliberate exception is the candlestick readout plate, which is a
  legibility device rather than a background — it stays on the theme colour.
- `still=<float 0..1>` returns the Figure at that point through the animation instead of
  encoding. This powers the live preview *and* the PNG still export, so it has to be the
  real frame — `save_still()` renders it at full output resolution for thumbnails. Every
  renderer must support it.
- `progress(i, n)` is passed straight to matplotlib's `progress_callback`.

To add a chart type: write the function, add an entry to `CHARTS` at the bottom of
`renderers.py`. The UI builds its chart list from `/api/meta`, so it appears
automatically. Chart-specific form fields need a `data-for="yourchart"` attribute in
`index.html`.

## Conventions that matter

**Themes.** All colour lives in the `THEMES` dict at the top of `renderers.py`. Never
hardcode a colour inside a renderer. Adding a theme requires no other change.

**Alpha.** Transparent renders swap codec *and* container — h264 in an MP4 has no alpha
channel, so they go out as ProRes 4444 in a `.mov`. `renderers.output_extension()` is the
single source of truth for which; `app.py` asks it rather than deciding separately.

**Log axes.** `_log_ok()` gates on the data as well as the setting — a range reaching zero
can't be drawn on a log axis, and falling back to linear beats failing the render. Two
things break silently if you forget them: y-limits need multiplicative padding (`_ylim`
handles both), and any offset expressed as a fraction of axis height has to become a ratio
(`_offsetter` returns the right function for the scale — the timeline's callouts use it).

**Moving averages.** `_fetch_with_ma()` pulls history from before the chart's start so the
averages are already warm on the first bar drawn. It returns averages over the *full*
fetched index; the caller aligns them to whatever bars it draws, with `ffill=True` where
bars have been resampled. Compute them before any rollup, so "50-day" means fifty days
even when each candle is a week.

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
- Bar race row ordering can look unsettled if a rank flips in the final frames. Longer
  hold masks it.
- A transparent render is ProRes 4444, which is enormous next to h264 — hundreds of MB for
  a 1080p60 clip. That's inherent to an edit-ready intermediate, but it makes the alpha
  path a poor fit for the Vercel Blob upload on a metered plan.
- Charts with moving averages cache separately from the same chart without them, because
  the run-up fetch changes the start date and so the cache key. Harmless, just surprising
  if you're watching `.cache/`.
- Moving averages are only on the line, candlestick and timeline charts. Comparison and
  race draw several tickers already, and averages on top would be unreadable.

## Roadmap

Near term, in rough priority order:

1. Reload a past render's config from the queue. Jobs already carry their `cfg`; the UI
   just can't reach it, so "same chart, but AMD" means retyping everything. Persist the
   form to `localStorage` at the same time — a reload currently resets to NVDA.
2. Intraday interval option — needed for same-day coverage of Fed days and earnings gaps.
3. Encoder preset exposed in the UI (`final` now defaults to `medium`).
4. Brand kit: save theme, footer, and default title format as a named preset. A logo
   watermark belongs here too — the footer is text-only today.
5. Batch render — one config, many tickers, queued. Needs cancel-in-flight first: cancel
   only works while a job is still queued, which a batch makes painful. Raising from the
   `progress()` callback aborts an encode cleanly.
6. Auto-annotations on the timeline chart — earnings dates and splits from the data
   source, rather than typing every callout by hand.
7. Benchmark overlay: draw SPY muted behind any single-ticker chart.
8. Adjusted vs raw closes as an explicit choice. `auto_adjust=True` is hardcoded in
   `_yahoo`, and Yahoo and Stooq adjust differently enough to change the total return
   being narrated — see the note in the README.

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

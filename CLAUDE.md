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

**The x axis depends on the interval.** Daily bars go on a real date axis; intraday bars go
on their *position*, with the timestamps moved onto the tick labels. This is not a style
preference — five-minute bars across a week are about two thirds overnight by wall clock,
so a date axis spends most of the frame drawing a flat line between sessions. Any renderer
that plots against time must take `x` from `_x_values(index, intraday)`, pass
`x_dates=not intraday` to `_style_axes`, and call `_position_ticks` when intraday. Label
text goes through `_range_label` and `_stamp` so a one-day chart reads `09:30` and a
multi-day one reads `06 Aug`. Anything that maps a date onto the axis — the timeline's
callouts, the race clock — has to resolve to a bar position too, not to a coordinate.

**Anything per-year is per-bar.** `sqrt(252)` is a count of daily bars. Annualising against
it on five-minute returns understates volatility by about 8.8x. Use
`datasrc.periods_per_year(interval)`.

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
- yfinance breaks periodically when Yahoo changes their endpoints. Daily renders survive
  it — `data.py` falls through to Stooq and the footer names the source. Intraday does not:
  Stooq serves daily bars and coarser, so there is nothing to fall through to.
- Intraday is therefore unavailable on the serverless deploy, which ships without yfinance
  to stay under the bundle ceiling. `/api/meta` reports `intraday: false` there and the
  interface drops the option rather than offering one that always fails.
- Bar race row ordering can look unsettled if a rank flips in the final frames. Longer
  hold masks it.

## Roadmap

Near term, in rough priority order:

1. Encoder preset exposed in the UI (`final` now defaults to `medium`).
2. Brand kit: save theme, footer, and default title format as a named preset.
3. Batch render — one config, many tickers, queued.
4. Intraday on a deploy. It is local-only while Stooq is the sole serverless source; a
   licensed feed with an intraday endpoint would close it, and that purchase is already
   required before a paid tier ships.

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

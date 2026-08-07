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
- yfinance breaks periodically when Yahoo changes their endpoints. A Stooq fallback in
  `data.py` would make this robust; not yet written.
- Daily bars only. Intraday needs `interval="5m"` and is limited to ~60 days of history.
- Bar race row ordering can look unsettled if a rank flips in the final frames. Longer
  hold masks it.

## Roadmap

Near term, in rough priority order:

1. Stooq fallback in `data.py` so renders don't fail when yfinance breaks.
2. Intraday interval option — needed for same-day coverage of Fed days and earnings gaps.
3. Encoder preset exposed in the UI (`final` now defaults to `medium`).
4. Brand kit: save theme, footer, and default title format as a named preset.
5. Batch render — one config, many tickers, queued.

### Cinematography — camera moves and transitions

Not started, noted so the design isn't painted into a corner before it happens. The idea:
push in on the last few months as the line head advances, drift the frame while a candle
prints, cut or cross-fade from the line chart into the compare chart. A locked-off frame
is a big part of why these read as charts rather than as footage.

Roughly in the order it would need building:

- **A camera is a function of normalised time.** Every renderer sets `set_xlim` /
  `set_ylim` once before the animation and never touches them again. A camera is those
  two calls moved inside `draw(i)`, driven by keyframes evaluated through `ease()`. It
  has to stay a pure function of `i / n_frames` — anything that accumulates per frame
  makes `still=` show a different image than the render produces, and that preview
  contract is the constraint the whole feature has to respect.
- **Zoom and pan are cheap, rotation is not.** Animated limits give push-in, pull-out
  and drift for free. Rotating the chart plane means either `mplot3d` — which discards
  the axis styling in `_style_axes` and looks nothing like the current output — or an
  affine transform applied to the finished frame. Prefer the latter, in the ffmpeg pass
  rather than in matplotlib.
- **Transitions imply more than one clip.** Today a render is one chart type, one
  `FuncAnimation`, one `_export()`. Cross-fading line into candles means rendering
  segments separately and joining them with an ffmpeg filter graph, so `render()` grows
  a shot list and the per-frame progress reported to `/api/jobs` has to span segments
  instead of counting frames of a single animation.
- **Ship presets, not a keyframe editor.** Three or four named moves — slow push, reveal
  and settle, whip to compare — consistent with the rest of the tool. If it needs a
  timeline UI, it's the wrong feature.

Measure the cost early. A concat pass lands on top of encode times that are already the
slowest thing here at `max`, and animated limits force a full redraw per frame.

Commercially this is meant to be its own plan rather than part of the base tier — around
$40/month, pencilled in rather than decided. The cost note above is the argument for that
split: cinematic renders will be the most expensive ones the product runs, so the tier has
to price compute and not just access. It can't ship before the licensed feed below either
way.

### Further out

This is being explored as a product. That means watermarking on a free tier,
render credits, the cinematography plan above, and eventually an API endpoint that accepts
a config and returns an MP4.
**Before any of that ships, the data source must be replaced with a licensed feed** —
yfinance scrapes Yahoo and redistributing that data to paying users is not permitted.
Tiingo, Twelve Data, EOD Historical and Polygon all license end-of-day US equities in the
$30-100/month range. That is a fixed cost rather than a per-user one, so roughly one
cinematography subscriber covers the feed and everything past that is compute and margin —
but it has to be covered before the first paying user, not after.

## Style

Python: standard library plus the four deps, no frameworks beyond Flask. Comments explain
*why*, not *what* — the existing comments are the reference for tone. Keep functions flat
and readable over clever.

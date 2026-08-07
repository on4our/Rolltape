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

Everything is pip, ffmpeg included — `imageio-ffmpeg` ships a static build as the
fallback. An ffmpeg already on PATH still wins when there is one.

## Architecture

```
app.py          Flask routes, single-threaded render queue, config validation
render_job.py   Both sides of the render subprocess — spawner and child entry point
renderers.py    Six chart types + themes + easing + camera + ffmpeg export
data.py         Yahoo fetch (yfinance), Stooq fallback, CSV disk cache, demo generator
config.py       Env-var configuration; every default reproduces the local setup
storage.py      Where a finished render lands and what URL plays it
jobs.py         The render job registry
presets.py      Named brand kits, saved to one JSON file
templates/      One HTML file, inline CSS and JS, no build step
outputs/        Rendered MP4s
```

Deployment support sits off to the side and nothing local reads it: `Dockerfile`.

There is deliberately no frontend build step and no database. Job state is an in-memory
`OrderedDict` in `jobs.py` that resets on restart. `config.py`, `storage.py` and
`jobs.py` exist as seams so the app can boot on a read-only filesystem — they are not an
abstraction layer to grow. Don't add a build pipeline or an ORM without a concrete reason. Brand kits are the one
exception and the only state meant to survive a restart: `presets.py` keeps them in a
single JSON document, written atomically and read fresh on every call.

## How a render works

1. Browser POSTs config JSON to `/api/render`.
2. `clean_config()` normalises and validates it. **All validation lives here** — the
   renderers assume a clean config and will raise unhelpfully otherwise. (It is currently
   thinner than that claim: theme, aspect, easing, metric and the dates go through
   unchecked, and a bad theme silently renders as Midnight.)
3. Job goes on a `Queue`; one worker thread drains it.
4. `render_job.run()` spawns a child process, which calls
   `renderers.render(cfg, path, progress)` — dispatching on `cfg["chart"]` via the `CHARTS`
   registry — and streams progress back over stdout as one JSON object per line.
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

**Quality tiers.** A tier owns `crf` and `preset` outright, but only *seeds* `fps` and
`res` — the slate overrides those two independently. `clean_config()` resolves both to
concrete numbers, so `cfg["fps"]` and `cfg["resolution"]` are always set and nothing
downstream re-derives them from the tier. `SIZES` is keyed by the short side (720, 1080,
1440), and the aspect decides which side that is.

**Date ranges.** Same shape: `RANGES` in `data.py` declares the presets, `clean_config()`
resolves the chosen one into concrete `start`/`end`/`interval`/`sessions`, and the
renderers never learn that presets exist. `range: "custom"` (or no `range` at all, which is
what an older API caller sends) uses the posted dates instead. Presets deliberately leave
`end` as None — Yahoo treats an explicit end as exclusive, so pinning it to today drops
today's bar. Renderers ask for their window with `datasrc.window(cfg)` rather than reading
`cfg["start"]` directly, so another knob doesn't mean editing six call sites. Adding a
preset is one entry in `RANGES`; the UI builds its buttons from `/api/meta`.

**Date labels follow the span.** `_axis_fmt()` picks the tick format from how much time is
on screen and `_range_label()` does the same for subtitles. A window shorter than a couple
of months under the old fixed `%b %Y` printed the same month at every tick, so any renderer
drawing a date axis passes its index in.

**Alpha.** Transparent renders swap codec *and* container — h264 in an MP4 has no alpha
channel, so they go out as ProRes 4444 in a `.mov`. `renderers.output_extension()` is the
single source of truth for which; `app.py` asks it rather than deciding separately. `crf`
and `preset` are x264 settings and are ignored on that path — ProRes is quality-driven by
`-qscale:v`, so the tier still picks the frame size but stops controlling the bitrate.

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

**Camera.** `Camera` animates the axis limits that each chart used to nail down once. It
is *planned*, not accumulated: every window is worked out before the first frame is drawn
and then read back by index. That is not a performance choice — `still=` asks for frame 200
without drawing the 199 before it, so a camera that nudged its limits each frame would hand
the still export a different frame than the video. Anything you add must keep that
property; if you need smoothing, smooth the planned array (`_smooth` is zero-phase, so the
frame anticipates rather than lags) rather than the live limits.

Two rules hold across every move. The reveal head is never out of shot — losing it reads as
a bug, not a camera. And the vertical follows the horizontal by the frame's own travel, so a
camera zooms rather than stretching one axis; that is also what makes a pull back's final
frame identical to a locked one. `locked` is the default and reproduces the pre-camera
framing exactly, which is why adding this changed no existing output — `extent` and
`rest_y` are how a renderer tells the camera what its own resting frame was. Charts built
from ranked rows (`bars`, `race`) have no plane to move over and never construct one.

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

**Threading and processes.** matplotlib's pyplot state is global, so `DRAW_LOCK` serialises
the drawing `app.py` does itself — previews and stills. Renders are not on that lock:
`render_job.run()` puts each one in its own process, which is what keeps the preview
answering during a seventy-second render and what stops an OOM-killed ffmpeg from taking
the server with it. Keep it that way. If you ever need renders to draw in-process again,
you are reintroducing the freeze that motivated the split, so don't — and one worker thread
draining the queue is still what bounds CPU, not the lock.

## Known rough edges

- **Render time is matplotlib, not ffmpeg.** A 7.5s 1080p60 clip takes ~90s, and drawing
  the 450 frames is nearly all of it. The x264 preset spans only ~3s of that (veryfast
  3.5s, medium 4.3s, slow 6.5s), and medium and slow produce the same file size. Anything
  that meaningfully speeds up a render has to make `_new_fig`/draw cheaper — blitting,
  reusing artists between frames, or rendering in parallel processes. Chasing the encoder
  settings is not worth it; that was measured, not assumed.
- `clean_config()` validates less than the contract above claims. Theme, aspect, easing,
  metric and both dates pass through unchecked — a typo'd theme silently renders as
  Midnight — and `duration` has no upper bound, so one request can queue tens of thousands
  of frames. `color_by_sign` is read by `render_bars` but stripped by `clean_config`, so the
  option is unreachable.
- `preset=slow` on the `max` quality tier is genuinely slow — roughly 70s for a 7.5s
  1080p60 clip — and needs enough memory that a small container host may OOM-kill it. That
  now costs you the one render rather than the server, and the job reports the memory hint
  instead of a signal number. `final` uses `medium` for the same reason.
- yfinance breaks periodically when Yahoo changes their endpoints. Daily renders survive
  it — `data.py` falls through to Stooq and the footer names the source. Intraday does not:
  Stooq serves daily bars and coarser, so there is nothing to fall through to.
- Intraday is therefore also the one feature that needs yfinance installed rather than
  merely working. `/api/meta` reports `intraday: false` when it is missing and the
  interface drops the option rather than offering one that always fails.
- Bar race row ordering can look unsettled if a rank flips in the final frames. Longer
  hold masks it.
- `clean_config()` holds all validation and has no tests of its own.
- Cancelling only works on a queued job. Killing an in-flight render is now a matter of
  signalling the child, but nothing in the UI calls the endpoint that would do it.
- `still=` maps 0..1 across the reveal, not across reveal + hold, so the preview scrub
  tops out at the first hold frame. That was invisible while every hold frame was
  identical; with `follow` the settle happens *during* the hold, so its final wide frame
  is the one frame you cannot preview or save as a thumbnail. Widening the mapping would
  move the frame every existing scrub position points at, so it wants doing deliberately
  rather than as a side effect.
- Charts with moving averages cache separately from the same chart without them, because
  the run-up fetch changes the start date and so the cache key. Harmless, just surprising
  if you're watching `.cache/`.
- Moving averages are only on the line, candlestick and timeline charts. Comparison and
  race draw several tickers already, and averages on top would be unreadable.

## Roadmap

Near term, in rough priority order.

1. Make `clean_config()` match its own contract: validate easing and metric, and bound
   `duration`.
2. Batch render — one config, many tickers, queued. The render subprocess is the piece
   that was missing; the queue already handles the rest.
3. Frame-drawing speed — the only lever that actually shortens a render. See the first
   known rough edge for where the time really goes.
4. Reload a past render's config from the queue. Jobs already carry their `cfg`; the UI
   just can't reach it, so "same chart, but AMD" means retyping everything.
5. Auto-annotations on the timeline chart — earnings dates and splits from the data
   source, rather than typing every callout by hand.
6. Benchmark overlay: draw SPY muted behind any single-ticker chart.
7. Adjusted vs raw closes as an explicit choice. `auto_adjust=True` is hardcoded in
   `_yahoo`, and Yahoo and Stooq adjust differently enough to change the total return
   being narrated — see the note in the README.

Done since this list was last rewritten: the Stooq fallback, renders out of process,
intraday intervals, date-range presets, camera moves, log price axes with moving
averages, the encoder preset (`final` moved from `slow` to `medium`, with an
Auto/Faster/Slower override in the UI) and brand kits.

### Cinematography — transitions

Camera moves have shipped: `Camera` in `renderers.py`, four named moves, planned rather
than accumulated so `still=` stays honest. See the **Camera** convention above. What this
section still describes is the half that hasn't been built — cutting or cross-fading from
the line chart into the compare chart.

- **Rotation stays off the table.** Animated limits gave push-in, pull-back and follow for
  free. Rotating the chart plane means either `mplot3d` — which discards the axis styling
  in `_style_axes` and looks nothing like the current output — or an affine transform
  applied to the finished frame. Prefer the latter, in the ffmpeg pass rather than in
  matplotlib.
- **Transitions imply more than one clip.** Today a render is one chart type, one
  `FuncAnimation`, one `_export()`. Cross-fading line into candles means rendering
  segments separately and joining them with an ffmpeg filter graph, so `render()` grows
  a shot list and the per-frame progress reported to `/api/jobs` has to span segments
  instead of counting frames of a single animation.
- **Ship presets, not a keyframe editor.** The camera followed this rule and should stay
  that way: named moves, consistent with the rest of the tool. If it needs a timeline UI,
  it's the wrong feature.

Measure the cost early. A concat pass lands on top of encode times that are already the
slowest thing here at `max`, and the camera already forces a full redraw per frame.

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

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
render_job.py   Both sides of the render subprocess — spawner and child entry point
renderers.py    Six chart types + themes + easing + ffmpeg export
data.py         Yahoo fetch (yfinance), Stooq fallback, CSV disk cache, demo generator
config.py       Env-var configuration; every default reproduces the local setup
storage.py      Where a finished render lands and what URL plays it
jobs.py         The render job registry
templates/      One HTML file, inline CSS and JS, no build step
outputs/        Rendered MP4s
```

Deployment support sits off to the side and nothing local reads it: `Dockerfile`.

There is deliberately no frontend build step and no database. Job state is an in-memory
`OrderedDict` in `jobs.py` that resets on restart. `config.py`, `storage.py` and
`jobs.py` exist as seams so the app can boot on a read-only filesystem — they are not an
abstraction layer to grow. Don't add a build pipeline or an ORM without a concrete reason.

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

**Alpha.** Transparent renders swap codec *and* container — h264 in an MP4 has no alpha
channel, so they go out as ProRes 4444 in a `.mov`. `renderers.output_extension()` is the
single source of truth for which; `app.py` asks it rather than deciding separately. `crf`
and `preset` are x264 settings and are ignored on that path — ProRes is quality-driven by
`-qscale:v`, so the tier still picks the frame size but stops controlling the bitrate.

**Motion.** `ease()` maps normalised time to progress; `_plan()` maps frame index to a
position along a densely-interpolated series. Series are upsampled to ~2x the frame count
before animating so the line head moves smoothly rather than hopping between daily closes.
Any time-based motion should be frame-rate independent — see the race renderer's
`1 - exp(-11/fps)` smoothing rather than a fixed per-frame constant.

**Mobile CSS.** The narrow-screen media query sits at the *end* of the stylesheet and must
stay there. It has the same specificity as the base rules, so moving it earlier silently
breaks the mobile layout. Inputs are 16px on mobile because Safari zooms the page for
anything smaller.

**Threading and processes.** matplotlib's pyplot state is global, so `DRAW_LOCK` serialises
the drawing `app.py` does itself — previews and stills. Renders are not on that lock:
`render_job.run()` puts each one in its own process, which is what keeps the preview
answering during a seventy-second render and what stops an OOM-killed ffmpeg from taking
the server with it. Keep it that way. If you ever need renders to draw in-process again,
you are reintroducing the freeze that motivated the split, so don't — and one worker thread
draining the queue is still what bounds CPU, not the lock.

## Known rough edges

- **An open-ended date range caches forever.** `_cache_path()` hashes `end` as the literal
  string `"None"`, so a range with no end date keeps hitting the same cache file — and an
  empty end date is what the UI ships by default. Render a ticker on Monday, render it
  again on Friday, and you get Monday's chart with no warning and nothing in the footer.
  `Clear price cache` is the only way out and nothing tells you to press it. This is the
  most damaging bug in the repo: it puts stale prices on camera. Fold the last market close
  into the cache key, or expire the file on mtime.
- `clean_config()` validates less than the contract above claims. Theme, aspect, easing,
  metric and both dates pass through unchecked — a typo'd theme silently renders as
  Midnight — and `duration` has no upper bound, so one request can queue tens of thousands
  of frames. `color_by_sign` is read by `render_bars` but stripped by `clean_config`, so the
  option is unreachable.
- `preset=slow` on the `max` quality tier is genuinely slow — roughly 70s for a 7.5s
  1080p60 clip — and needs enough memory that a small container host may OOM-kill it. That
  now costs you the one render rather than the server, and the job reports the memory hint
  instead of a signal number. `final` uses `medium` for the same reason.
- Daily bars only. Intraday needs `interval="5m"` and is limited to ~60 days of history.
  Note that Stooq's CSV endpoint is daily-grain, so intraday would have no fallback — it
  is the one feature that breaks outright when Yahoo does.
- Bar race row ordering can look unsettled if a rank flips in the final frames. Longer
  hold masks it.
- `clean_config()` holds all validation and has no tests of its own.
- Cancelling only works on a queued job. Killing an in-flight render is now a matter of
  signalling the child, but nothing in the UI calls the endpoint that would do it.

## Roadmap

Near term, in rough priority order. The Stooq fallback that used to head this list has
shipped; so has moving renders out of process.

1. Expire the price cache on open-ended ranges — see the first known rough edge. Nothing
   else on this list matters as much as the tool not drawing stale prices.
2. Make `clean_config()` match its own contract: validate theme, aspect, easing, metric and
   the dates, and bound `duration`.
3. Intraday interval option — needed for same-day coverage of Fed days and earnings gaps.
4. Encoder preset exposed in the UI (`final` now defaults to `medium`).
5. Brand kit: save theme, footer, and default title format as a named preset.
6. Batch render — one config, many tickers, queued. The render subprocess is the piece
   that was missing; the queue already handles the rest.

Done: Stooq fallback in `data.py`, so a render survives Yahoo changing its endpoints.

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

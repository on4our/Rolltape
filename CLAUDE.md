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
python app.py --host 0.0.0.0     # reachable from phone on the same wifi

ROLLTAPE_FMP_KEY=...             # the licensed feed answers first
ROLLTAPE_FMP_HISTORY_YEARS=5     # Starter's ceiling; 30 on Professional
ROLLTAPE_LICENSED_ONLY=1         # and the scraped fallbacks are refused entirely
ROLLTAPE_FRED_KEY=...            # and the ticker field also takes economic series
```

Everything is pip, ffmpeg included — `imageio-ffmpeg` ships a static build as the
fallback. An ffmpeg already on PATH still wins when there is one.

## Architecture

```
app.py          Flask routes, single-threaded render queue, config validation
render_job.py   Both sides of the render subprocess — spawner and child entry point
renderers.py    Seven chart types + themes + easing + camera + ffmpeg export
data.py         FMP and Twelve Data (licensed), Yahoo, Stooq — in that order, FRED off to
                one side for economic series, CSV cache, the corporate events the timeline
                marks by itself, and the symbol search behind the ticker field
fundamentals.py Income statements — FMP then Yahoo — and the waterfall's bridges. A
                separate seam from data.py because a statement is not a price series
config.py       Env-var configuration; every default reproduces the local setup
storage.py      Where a finished render lands and what URL plays it
jobs.py         The render job registry
presets.py      Named brand kits, saved to one JSON file
examples.py     The three configs the landing page draws as its showcase
signups.py      Email capture — a list provider when configured, a file otherwise
testsupport.py  Generated prices for the suite. Nothing the app runs imports it
templates/      The app, the landing page, the pricing page and an error page — inline
                CSS and JS
                each, no build step
outputs/        Rendered MP4s
test_*.py       The suite — see Tests below
```

Deployment support sits off to the side and nothing local reads it: `Dockerfile`.

Two directories hold no code the app runs. `docs/` is the commercial plan — `pricing.md`,
`acquisition.md`, `revenue-projection.md` — and `scripts/revenue_model.py` regenerates
every number in the last of those. They are decided-but-unshipped, so nothing in the app
enforces any of it; treat them as the source of truth for product direction and this file
as the source of truth for the code.

There is deliberately no frontend build step and no database. Job state is an in-memory
`OrderedDict` in `jobs.py` that resets on restart. `config.py`, `storage.py` and
`jobs.py` exist as seams so the app can boot on a read-only filesystem — they are not an
abstraction layer to grow. Don't add a build pipeline or an ORM without a concrete reason. Brand kits are the one
exception and the only state meant to survive a restart: `presets.py` keeps them in a
single JSON document, written atomically and read fresh on every call.

**One process, or the registry splits.** Because job state is that dict, `--workers 1` in
the Dockerfile is load-bearing rather than a tuning choice — a second worker process is a
second registry, so renders start, finish, and never appear in the UI that asked for them.
Threads are fine and the Dockerfile uses eight of them; processes are not. Hosting this
for more than one user means a shared store behind the `jobs.py` seam first.

## How a render works

1. Browser POSTs config JSON to `/api/render`.
2. `clean_config()` normalises and validates it. **All validation lives here** — the
   renderers assume a clean config and will raise unhelpfully otherwise. (It is currently
   thinner than that claim: theme, aspect, easing and metric go through unchecked, and a
   bad theme silently renders as Midnight.)
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
`renderers.py`, and add a fixture to `CHART_FIXTURES` in `test_render.py`. The UI builds
its chart list from `/api/meta`, so it appears automatically. Chart-specific form fields
need a `data-for="yourchart"` attribute in `index.html`. The fixture is what makes the
suite cover the new chart at all — several tests iterate the whole registry, so a chart
missing from it is silently untested rather than failing.

A chart that doesn't draw from prices also has to be taken out of the controls that assume
it does — the waterfall is the only one so far, and `usesRange()` in `index.html` is where
that happens for the date range, the custom dates and the bar interval. Leaving them on
screen would imply a choice the render is about to ignore, which is the same reason a
locked camera hides travel and vertical.

## Tests

```bash
python -m unittest              # all 440, about 20 seconds
python -m unittest test_camera  # one module
```

No network and no ffmpeg. Each source is forced to fail so the one below it runs, Stooq,
FMP, Twelve Data and FRED answer from recorded response samples, everything else draws from
`testsupport.py`, and the two end-to-end encode tests skip themselves when there is no
ffmpeg to call. So the suite is fast enough to run on every change, and there is no excuse
for not having run it.

**There is no generated-data mode in the app**, and reintroducing one would be a mistake: a
chart of invented prices is indistinguishable from a real one three steps later in a video
editor. The generator lives in `testsupport.py`, which nothing the app runs imports, and
tests reach it three ways — `patch_fetch(case)` and `patch_income(case)` in-process, and
`seed_cache(...)` for the two tests that spawn a real render subprocess. The second is the interesting one: rather than a
flag handed across the process boundary, the child's disk cache is filled in advance and
the ordinary cache hit in `data.fetch` does the rest, so the test exercises a production
path instead of a test-only one.

- `test_app.py` — `clean_config()`, every input the interface can send; the pricing page's
  numbers against `docs/pricing.md`; and the error page's HTML-versus-JSON split.
- `test_data.py` — the four-source fallback order, FMP and Twelve Data parsing, the plan
  horizon, range presets, cache freshness, attribution, and the corporate-event lookup.
- `test_fundamentals.py` — the statement fetch and, more importantly, the bridge
  arithmetic: that every bridge lands on the total it names, under a missing line, an
  overshooting expense split, and a loss.
- `test_render.py` — the export path: backgrounds, date labelling, stills, every theme, and
  the timeline's callout layout.
- `test_camera.py` — the planned limits behind each move.
- `test_render_job.py` — the two-process protocol, the inherited cache, and preview latency
  under load.
- `test_presets.py` — brand kit persistence and the title template.
- `test_landing.py` — the landing page, the showcase stills and email capture.
- `test_tickers.py` — the symbol lookup and the series endpoint behind the ticker field.
- `test_economic.py` — every place a FRED symbol has to behave differently from a ticker:
  the routing, the three refusals, the units, and the labelling. The generator in
  `testsupport.synthetic_economic` draws flat OHLC deliberately — a range there would be
  testing a frame the app can never produce, and would quietly pass the candlestick path
  that exists to be refused.

Two things the suite is built around, both worth preserving:

- **Draw stills, don't encode.** `test_render.py` renders one frame and reads the pixels
  back, which exercises the same figure scaffolding a video render uses without paying
  for the encode. A test that needs a whole clip is nearly always a test that wanted one
  frame.
- **The tests that matter most are the ones about the properties, not the pictures.** That
  a locked camera reproduces exactly the pre-camera framing; that a preview still answers
  during an in-flight render; that a transparent export has no opaque backdrop. Those are
  the conventions below, made enforceable — when you add one to this file, ask what would
  fail if someone ignored it.

## Conventions that matter

**Themes.** All colour lives in the `THEMES` dict at the top of `renderers.py`. Never
hardcode a colour inside a renderer. Adding a theme requires no other change.

Where a chart needs a colour for a *role* the palette doesn't name, derive it rather than
adding a hex — `_pillar_color()` picks the first series colour that is neither `up` nor
`down`, because two of the four themes open their palette with the same value they use for
a rise and `series[0]` would paint a waterfall's closing pillar identically to the change
beside it. Adding a theme still needs no edit here, which is the property that matters.

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

**Price sources.** `SOURCES` in `data.py` is the preference order and `_sources_for()` is
the only place it gets narrowed. Everything else (`_find_cached`, `_drop_superseded`, the
fetch loop) reads that one function, so another source is an entry in `SOURCES`, a fetcher
with the shared `(ticker, start, end, interval)` signature, an entry in the `fetchers` dict,
an entry in `SOURCE_KEYS` if it needs one, and nothing else.

**The symbol picks the catalogue, then the catalogue gets narrowed.** `_sources_for()` takes
the ticker as well as the window, and its first act is choosing between `SOURCES` and
`ECONOMIC_SOURCES` on the `FRED:` prefix. That ordering is the point: the two kinds are not
ranked against each other and never fall through into each other, because no price feed
publishes CPI and FRED publishes no tickers. A fallback only makes sense between sources
that could both answer the same question. Everything after the choice — the key check,
`LICENSED_ONLY`, `DAILY_ONLY`, the plan horizon — applies to whichever list was picked, so a
new economic source is an entry in `ECONOMIC_SOURCES` and the same four hooks a price source
needs.

**Statements are a second seam, not a fifth source.** `fundamentals.py` fetches income
statements and `SOURCES` there is FMP then Yahoo. It is a separate module because
`data.SOURCES` is a preference order over fetchers that all share one signature —
`(ticker, start, end, interval)` returning OHLCV — and a statement has none of those
arguments: it arrives per fiscal period, in line items, from a different endpoint. What it
does share is borrowed rather than restated: `data.keyed`, `data.covers` and
`data.fmp_rows` are public for exactly that, and `data.note_source` is the one writer
behind the footer, so a waterfall drawn off the licensed feed credits it without
`attribution()` learning a second path. Two sources rather than four because Stooq
publishes no fundamentals at all and Twelve Data's are on a plan above the $29 one wired
here — which also means a waterfall has nothing behind Yahoo and fails rather than falling
through, unlike every price chart.

**A source that can't serve the request is dropped, never asked to approximate it.** There
are three ways to be dropped and they exist for the same reason — a failed render is
recoverable and a wrong one that looks right is not:

- *No key.* A licensed source without one never spends a call finding that out.
- *Wrong interval.* Stooq is out for intraday, because answering a five-minute chart with
  daily bars and labelling it five-minute is the exact failure this rule exists for.
- *Past the plan's horizon.* FMP Starter reaches back five years and answers a longer
  window with a **short frame rather than an error** — a MAX request would come back as
  five years under a MAX label. `config.FMP_HISTORY_YEARS` is checked before the request,
  so the window falls through to a deeper source instead. This is the subtle one: it is
  enforced client-side because the API gives no signal, so a plan upgrade means changing
  the env var or the ceiling silently stays wrong in the safe direction.

**Corporate events fail soft, and never come half from one source.** `events()` is the
lookup behind the timeline's automatic callouts — earnings, splits and dividends, declared
in `EVENT_KINDS`. It reuses the price order through `_event_sources()` rather than beside
it, so the key checks, `LICENSED_ONLY` and the plan horizon all still apply; Stooq drops out
because it is a price CSV and publishes none of this. Two rules hold, and they pull in
opposite directions from `fetch` on purpose:

- *A kind arrives whole from one source or not at all.* A source that raises is passed over
  entirely rather than contributed from. Half of one feed's earnings dates plus half of
  another's is a set complete from neither that looks authoritative anyway — the same class
  of failure as labelling daily bars five-minute, somewhere nobody would check. An empty
  answer is a real answer: a company that never split has no splits.
- *A failed lookup costs the marks, not the render.* The opposite of what `fetch` does, and
  the reason is the same one: a chart without its prices is nothing, but a chart whose
  earnings lookup timed out is the chart that was asked for, missing an overlay. Failing the
  job over it would trade a recoverable outcome for one that isn't. `EVENT_TIMEOUT` is
  shorter than a price fetch's for the same reason the search's is shorter still — this runs
  inside `/api/preview`, on `DRAW_LOCK`.

Events cache to disk beside the prices but outside `_drop_superseded`'s glob, which matches
on `SYMBOL_` and would otherwise retire them as stale price frames — hence the dot in
`_events_path`. A test pins that.

**The footer names whichever sources actually answered**, all of them when a render mixed
sources, because a comparison chart pulling one ticker from a fallback is exactly when a
single label would be a lie. Yahoo is the one that stays silent: nobody to credit, and
nothing a viewer wouldn't already assume.

**Economic series.** A `FRED:`-prefixed symbol is one number per period rather than a price,
and `data._fred` returns it in the shared OHLCV shape with open, high, low and close all
equal to the observation. That is not a convenience — it is what the data is, since there is
no intra-period range to know — but it means the frame *looks* like a bar to anything that
doesn't check. Four things follow, and they are the whole convention:

- **Three pairings are refused, in `clean_config()`.** Candlesticks, because a chart of flat
  dashes reads as a market where nothing moved rather than as the wrong chart type; intraday
  ranges, because FRED publishes daily at best; and the bar chart's volatility metric,
  because `sqrt(252)` counts market sessions and a monthly series would come out about eight
  times too large. Same rule as a source that can't serve a request: refused, never
  approximated. `render_candles` repeats its own refusal for a renderer called directly.
- **Nothing is printed in a unit the series didn't declare.** `_unit_style()` in
  `renderers.py` reads FRED's prose units and handles the two that change how a number is
  drawn — percent and dollars — and returns a bare number for everything else. An unknown
  unit and a metadata lookup that failed land in the same place on purpose: a chart must
  never put a dollar sign on something nobody said was dollars. `_econ()` returning None is
  what routes an ordinary ticker back to `_money`, so a price chart is unchanged.
- **A rate moves in points, not percent.** `_headline()` reports `+0.5 pts` for a series
  denominated in percent and `+3.0%` for everything else. Unemployment 4.0 → 4.5 called
  "+12.5%" is the class of number a video gets corrected on.
- **Per-period means the series' own period.** `_econ_period()` reads the frequency off the
  metadata, so a `12` on monthly CPI is a 12-*month* average and `_fetch_with_ma()` warms it
  with a year of lead rather than twelve days. This is the same rule as "anything per-year is
  per-bar", one level further out: the interval says "daily" and the series says otherwise.

`economic_meta()` is where all four get their answers, and it **fails soft to the bare series
id**. A title is worth a request and is not worth a render. It caches to disk as well as in
memory because every render is a fresh process and would otherwise re-request on each job.

**The `FRED:` prefix does two jobs.** It is the namespace — a bare `GDP` or `T` could
plausibly be a ticker or a series, and guessing is how the wrong instrument gets drawn under
the right label — and it is the gesture that switches which service `search()` asks. A plain
query goes to Yahoo plus the built-in `LOCAL_SERIES` list, so typing "inflation" finds CPI
without spending a second round trip on every keystroke of every ticker anyone types. A
prefixed one goes to FRED's own search instead and reaches all 800,000 series. A bare `FRED:`
lists the built-in set, which is what makes the namespace discoverable by typing it.

There is no generated-price source and there must not be one — see Tests.

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

`ma_lag` holds them behind the reveal head, which is what an indicator that lags the price
looks like in motion. It is *planned* for the same reason the camera is: `ma_track()`
reworks the renderer's own frame-to-index array once, before the first frame, so `still=`
still answers for frame 200 without drawing the 199 before it. The delay is in seconds —
frame-rate independent, like every other move — capped as a share of the reveal so a short
clip isn't swallowed by it, and closed over the last quarter so the final frame is the same
chart a lagged and an unlagged render both end on. `none` is the default and reproduces
the pre-lag output exactly. A renderer opts in by drawing its averages from that second
array rather than from `cut`; drawing them from `cut` is how they keep pace.

**Callouts.** `plan_callouts()` gives every timeline callout a row and a text anchor before
the first frame, and `timeline_notes()` is what feeds it — the looked-up events merged with
the typed ones, a typed callout on the same bar replacing the event it lands on. Planned
rather than accumulated, for the reason the camera is: `still=` asks for frame 200 without
drawing the 199 before it, and a layout that re-solved itself as the camera moved would also
make labels jump rows mid-shot. Three things are load-bearing:

- **Nothing is dropped; only the text thins.** Every event keeps its dot and stem, and a
  label that has nowhere to go is simply not written. A chart showing four of a year's eight
  earnings dates would read as the complete set — and since every one of those labels says
  the same word, losing some of the text loses no information at all.
- **The collision test is a rectangle, not a column.** A row is a lift above each callout's
  *own* point rather than a shared height, so two labels one row apart whose prices differ
  by that same lift land on exactly the same line. A horizontal-only check misses precisely
  the overlap that shows up on a real chart.
- **The frame is sized before the layout, never from it.** The timeline pads its top when it
  has callouts at all, because they lift off their own points and the top row would land
  outside the frame otherwise. Deriving that padding from how the rows came out would be
  circular — the layout is solved against the frame. A timeline with no callouts keeps
  exactly the framing it always had, and a test pins that.

Label widths are estimated from character counts rather than measured, because measuring
needs a draw and this settles before the first frame. `_CHAR_W` over-estimates deliberately:
too wide costs a label that would have fitted, too narrow costs an overlap. `CALLOUT_RANK`
decides who wins a contested spot — typed, then split, then dividend, then earnings, which
is specific-before-repeated.
**Bridges close by construction.** The waterfall's whole claim is that its bars land on the
total the last one names, so `fundamentals.bridge()` only ever subtracts one *reported*
subtotal from another — cost of revenue is `gross profit - revenue`, not the filed cost
line. Summing components instead would stop landing on net income the moment a filer left
a line out or classified something unusually, and the error would surface on the tax bar,
which is the last place anyone would look for it. Three consequences worth keeping: a stage
whose inputs are missing is *dropped* rather than guessed at (no gross profit means one
operating-expenses bar, not three invented ones); the R&D/SG&A split draws whatever it
doesn't account for as a labelled residual rather than absorbing it; and a split that
overshoots the step falls back to one honest bar. `IncomeBridgeTests` is that arithmetic,
made enforceable.

Figures stay in the currency they were filed in and at the scale they were filed at —
nothing rescales on the way in, so `$60.9B` on screen is checkable against the filing.
`_compact` is what reads it out and `_compact_axis` is the axis version, which picks one
scale and one precision for every tick; `_money` is for prices and would print a quarter's
revenue as `$130,497,000,000`.

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

**The ticker field.** Two endpoints sit behind it and they answer different questions.

`/api/search` is the typeahead. `data.search()` merges a built-in symbol list with Yahoo's
search endpoint and ranks exact symbol over prefix over substring over a name-only match —
someone typing MU means Micron, not every company with those letters in its name. It never
answers with an error status: mid-word junk means "no suggestions yet", and a field that
turns red halfway through a symbol is worse than one that finds nothing. The built-in list
is a floor rather than a universe — it is what answers offline and before a round trip
could have finished — and Yahoo finds everything past it. That call is plain urllib rather
than yfinance on purpose, so the lookup still works on an install where a licensed feed or
Stooq is drawing the charts, and it runs whichever source that is: a suggestion is a symbol
and a name rather than a price, so it carries none of the display terms the licensed feed
exists to satisfy. A failed lookup is swallowed and never cached; caching it
would keep the field degraded long after Yahoo came back.

`/api/series` is the other half: the numbers a chart would be drawn from, without drawing
it. It takes the same body as `/api/preview`, so the range preset, the interval and the
per-chart ticker limit are the ones the render will use rather than a second set that can
quietly disagree. It touches no Figure, which is what keeps it off `DRAW_LOCK` and
answerable on a keystroke — a test asserts that. Two properties worth keeping: the summary
figures are computed from the whole frame and the closes thinned to a bounded number of
points *afterwards*, so the point cap can never move the number printed on screen; and a
ticker that doesn't resolve gets its own row rather than failing the request, because one
typo among six symbols shouldn't blank the other five.

On the client, the suggestion list works on the comma-separated token the caret is inside,
never the whole field — typing the third symbol of a comparison chart must not offer to
overwrite the first two. The readout refetches from one call inside `schedule()` that
compares only the part of the config deciding the window, rather than a listener on each of
the five controls that can change it; that is what stops a sixth being added later without
this noticing.

**Interface styling.** The chrome follows Stripe: a light ground with white cards, navy
text, indigo for the one thing you are meant to press. Every colour, shadow and focus ring
is a custom property in `:root` — nothing below it hardcodes a hex, the same rule `THEMES`
follows for the renderers. Three things are load-bearing rather than taste:

- Depth is a hairline *ring inside the box-shadow*, never a border. A border changes the
  box on focus and shifts the control by a pixel; the ring swaps for `--focus` and nothing
  moves. Adding `border:1px` to a `.ctrl` or a button reintroduces that jump.
- The preview viewport stays dark on the light chrome so a rendered frame reads as footage
  rather than as another panel — and so does the transparency checkerboard, which takes
  navy instead of the usual white. Three of the four themes draw light text that would
  disappear on a pale check.
- Nothing here reaches into `THEMES`. Chart colour and interface colour are separate
  vocabularies; the swatch dots are the only place a theme's colours appear in the chrome,
  and they arrive from `/api/meta` as data.

**Two rails, and both collapse.** The settings are split twice over, on two axes that don't
line up — which is why neither split alone was enough.

*Left or right* is what the chart is versus how it is presented. `#railLeft` holds chart
type and data, `#railRight` holds motion, camera, output, labels and the brand kit, and the
preview sits between them. One rail held all seven sections, and the half you weren't
working in was always in the way of the half you were; two columns also mean a long Data
section can no longer push the theme swatches off the bottom of the page. Adding a section
means picking a rail — subject on the left, treatment on the right.

*Open or closed* is per render versus per channel. Every section is a
`<details class="group">` carrying its label and a digest of its current values in the
`<summary>`; chart, data, motion and camera start open, and output, labels and the brand kit
start closed because they are picked once for a channel and never looked at again.
Collapsing that half took the single rail from 2,690px to 1,670px on the default chart, and
splitting it across two takes the left rail to 1,155px against the right's 577px — the
subject of the render is now within a screen of itself. Four things hold:

- **A closed section still feeds `config()`**, which is exactly why every one of them
  carries a `.digest`. `<details>` keeps its children in the DOM, so a section with no
  digest is a setting that changes the render with nothing on screen saying what it is set
  to. `SECTIONS` in the script is the list that fills them, and it is keyed by section id
  rather than by rail, so moving a section between rails is a markup change and nothing
  else. `RailSectionTests` in `test_app.py` fails if a new section misses either half, if
  a rail ends up empty, or if a set-once section drifts back into the left rail.
- Digests read from `state` where there is a choice, not from the DOM, so the summary
  cannot disagree with what the render will do. `slate()` refreshes them, and every control
  already routes through it — that is the one call site, not a listener per field.
- Which sections are open is the one thing kept in `localStorage`. It is a preference
  rather than app state, and the alternative is re-expanding a channel's theme and footer
  on every reload, which is the scrolling this removed. Guard the access — it throws rather
  than no-ops in a few privacy modes.
- **Three columns need the width for three columns.** `352px | 1fr | 320px` leaves a
  usable preview down to about 1,280px; below 1,180px the right rail folds back under the
  left one and both give up their own scroll, and below 940px the whole thing stacks with
  the preview first. The fold-back query sits between the two — after the base rules it
  overrides, before the narrow-screen one that has to stay last.

All four templates carry the same palette, and each holds its own copy of the `:root` block
because there is no build step to share one. That duplication is the deliberate cost of the
no-build-step rule, not drift — change a token in one and change it in all four. The two
public pages stay a step larger than the app throughout: `index.html` is a dense tool sized
for someone working in it, the others are prose sized for someone reading them once.

**The error page.** `error.html` is rendered for 404 and 500, and it is the one template
that pulls in no webfont — it renders when something is already broken, so it must not
depend on anything that could be the broken thing. It therefore takes whatever `system-ui`
resolves to rather than Inter, which is close enough on a page this short and worth not
blocking first paint on a font request when the network may be what failed. The handler
skips `/api/*`: the interface calls `.json()` on every API response, so an HTML body under
that prefix turns a missing route into a parse error instead of a message. Views that
already answer their own 404 through `jsonify` — `delete_preset`, the example stills — are
untouched, because an `errorhandler` only fires for a raised or aborted response. A 404
under `/outputs/` gets its own wording: those files outlive a restart, so a missing one was
removed rather than expired, and that is the likeliest way anyone reaches this page at all.

**Mobile CSS.** The narrow-screen media query sits at the *end* of the stylesheet and must
stay there. It has the same specificity as the base rules, so moving it earlier silently
breaks the mobile layout. Inputs are 16px on mobile because Safari zooms the page for
anything smaller. Both pages also carry `[hidden]{display:none !important}` after that
query, because a `display:` rule of their own outranks the attribute otherwise — that is
what makes the signup form actually leave when it sets `hidden`.

**The landing page.** `/` is the app unless `ROLLTAPE_LANDING` says otherwise, in which
case the page takes `/` and the app moves to `/app`. It is the one template rendered
through Jinja rather than served as a file: it is all content and no behaviour, so it
builds its chart list from the `CHARTS` registry the same way the app builds its own from
`/api/meta`, and adding a chart type needs no edit here. Its showcase frames come from
`examples.py` through `renderers.save_still()` — the actual renderer, cached to disk on
first request — because a page arguing that the output looks good cannot illustrate itself
with a mockup. A frame that won't draw 404s and the page describes the chart in words
instead; the price source being down is exactly when the rest of the page still has to
load. Anything laid over a `.wrap` sets `padding-top`/`padding-bottom` rather than the
shorthand, which would reset the horizontal padding `.wrap` owns and put text against the
bezel on a phone.

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
- `clean_config()` validates less than the contract above claims. Theme, aspect, easing and
  metric pass through unchecked — a typo'd theme silently renders as Midnight — and
  `duration` has no upper bound, so one request can queue tens of thousands of frames.
  `color_by_sign` is read by `render_bars` but stripped by `clean_config`, so the option is
  unreachable. The dates are the part that *is* checked: `_date()` refuses anything that
  isn't ISO and `resolve_window()` rejects an end on or before the start.
- `preset=slow` on the `max` quality tier is genuinely slow — roughly 70s for a 7.5s
  1080p60 clip — and needs enough memory that a small container host may OOM-kill it. That
  now costs you the one render rather than the server, and the job reports the memory hint
  instead of a signal number. `final` uses `medium` for the same reason.
- yfinance breaks periodically when Yahoo changes their endpoints. Daily renders survive it
  — `data.py` falls through to Stooq and the footer names the source. Intraday survives it
  only with a licensed key; Stooq serves daily bars and coarser, so without one there is
  nothing to fall through to.
- Intraday therefore needs a licensed key or yfinance installed. `/api/meta` reports
  `intraday: false` when there is neither, and the interface drops the option rather than
  offering one that always fails.
- **A MAX or 10Y chart does not come from the licensed feed on FMP Starter.** It falls
  through to Yahoo, draws in full, and the footer stops naming FMP — correct, and
  surprising if you are watching which source answered. Under `LICENSED_ONLY` the same
  render fails instead.
- **A moving average spends part of the horizon.** `_fetch_with_ma()` pulls its run-up from
  before the chart's start, and `_covers()` sees that earlier date rather than the one on
  screen — so a 200-day average costs 314 days of the five years and the deepest chart FMP
  will serve with one is about 4.1 years, not 5. It falls through to Yahoo and still draws
  correctly; it just quietly stops being a licensed render, which matters under
  `LICENSED_ONLY` where it fails instead. Checking the visible start rather than the fetched
  one would be wrong — the run-up is a real request and the plan really cannot serve it.
- **FMP's individual plans do not cover displaying data to end users or the public**, which
  is what every render is. That needs their Data Display and Licensing Agreement, quoted
  rather than listed, and it is unresolved. Twelve Data is wired and tested as the
  alternative — its $29 plan states display use is included — so switching is a key swap
  rather than a rewrite. **A revenue figure on screen is display use exactly as a price
  is**, so the waterfall sits under the same unresolved question — and the escape hatch is
  narrower there, because Twelve Data's fundamentals are on a plan above the one that
  would replace FMP for prices.
- Neither licensed feed's dividend and split adjustment has been compared against Yahoo or
  Stooq. Same class of discrepancy the README warns about for Yahoo versus Stooq, and the
  same thing roadmap item 6 is about — but unmeasured rather than merely unexposed, so
  check it before narrating a total return off a licensed render.
- The Twelve Data paging loop is capped at `TWELVEDATA_MAX_PAGES`. That ceiling is a guard
  against a paging bug becoming an unbounded request loop against a metered API, not a real
  limit — but a `max` render on a symbol with two centuries of history would silently start
  at the cap rather than at the listing.
- **Symbol search has no fallback feed.** None of the price sources publishes a search
  endpoint worth calling, so when Yahoo's is down the suggestions narrow to `LOCAL_SYMBOLS`
  and anything outside that list has to be typed in full. The chart still draws — only the
  suggestion goes missing — but it is the one part of the field with a single source behind
  it.
- `LOCAL_SYMBOLS` is a hand-maintained snapshot. A delisting or a ticker change keeps
  suggesting itself until someone edits the tuple, which is the price of having the field
  answer offline and on the first keystroke. Yahoo is the authority for everything past it,
  so the list stays short rather than trying to be a directory.
- Bar race row ordering can look unsettled if a rank flips in the final frames. Longer
  hold masks it.
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
- **The event parsers are written to documented response shapes, not to captured ones.**
  Every other fetcher in `data.py` was built with a real response in front of it; these
  three were not, which is why `_pick` accepts several spellings per field and a row that
  can't be read is skipped rather than raised over. The failure mode is therefore quiet —
  an endpoint that renamed a field gives an empty set, which is indistinguishable from a
  company that has never split. Worth one look at a live response per provider before
  anyone leans on these.
- **A dividend label is the cash amount, unadjusted.** `_dividend_label` prints what the
  feed returned, and FMP carries `adjDividend` alongside `dividend` for exactly the reason
  the README warns about elsewhere — a pre-split chart marked with post-split amounts is
  the same class of discrepancy as roadmap item 6, one layer down.
- Auto callouts are on the timeline chart only. The line chart has the same shape and could
  carry them, but it also has the live price readout in the same corner the labels lift
  into, so it wants a layout decision rather than a registry entry.
- The three kinds are fetched one request each, on the render *and* on the preview that
  precedes it. The disk cache means that's once per window rather than per keystroke, but
  a first preview with all three on is three round trips before a frame is drawn.
- Moving averages are only on the line, candlestick and timeline charts. Comparison and
  race draw several tickers already, and averages on top would be unreadable.
- **Rising is drawn green on an economic series too, and for unemployment or inflation that
  reads as good news.** The up/down colours say direction, which is all they say on a price
  chart — but a price going up and the jobless rate going up are not the same sentiment, and
  the colour is the first thing a viewer reads. Fixing it means knowing which way is "good"
  per series, which is nowhere in FRED's metadata and would be a guess. Typing a subtitle is
  the workaround; a per-series polarity flag is the real fix if this ever matters enough.
- **Most of FRED is federal statistics and free of copyright, but not all of it.** Some
  series are redistributed from private providers under their own terms, and the `FRED:`
  search reaches those as readily as the rest. `LOCAL_SERIES` is all official statistics;
  anything found past it is unvetted, and its FRED page names the source's terms. This is
  the same unresolved shape as FMP's display licence, one level less pressing because the
  default set is clean.
- **Economic series have no fallback at all.** One source, no second opinion — when FRED is
  down or the key is wrong, an economic render fails and there is nothing below it. That is
  correct rather than unfortunate (nothing else publishes these numbers), but it is a
  different failure profile from a price chart, which has three sources under it.
- A series' metadata is cached to disk indefinitely and never revalidated. Titles and units
  effectively never change, but a series that gets rebased — a new index year — keeps the old
  unit string in its subtitle until `.cache/` is cleared.
- `LOCAL_SERIES` is hand-maintained for the same reason `LOCAL_SYMBOLS` is, and carries the
  same cost: a discontinued series keeps suggesting itself until someone edits the tuple.
- **A waterfall has one source below the licensed one and nothing below that.** Yahoo's
  fundamentals endpoint is the whole fallback, so when it moves the render fails rather
  than falling through the way a daily price chart does. That is the honest outcome and
  it is also a thinner floor than any other chart stands on.
- **Neither statement fetcher has been checked against a live response from this repo.**
  Both are written to the documented shapes and covered by recorded samples in
  `test_fundamentals.py`, which is the same standard the FMP and Twelve Data price
  fetchers are held to — but those were written with a key in hand and these were not.
  The first thing to do with a real key is confirm the field names, particularly
  `fiscalYear` versus `calendarYear` across FMP's stable and v3 paths.
- **Yahoo reports no fiscal period label**, so `_yahoo_label` derives one from the period
  end date. The annual form is right for very nearly every filer; quarterly is the weak
  half, because a company whose year is offset from the calendar numbers its quarters
  differently from the calendar quarter they end in. FMP reports the real label and is
  tried first, so this is the degraded answer rather than the usual one.
- The waterfall ignores `range`, `start`, `end` and `interval` entirely — `usesRange()`
  hides them in the interface, but an API caller can still post them and they do nothing.
- A hand-typed bridge whose totals disagree with its own running sum draws the totals it
  was given, because on that path the author's number is the authority. The fetched
  bridges close by construction and can't hit this.
- **Statement figures and price figures may not agree about a company.** Nothing
  cross-checks them, and the adjustment question in the note above applies here too: a
  waterfall off FMP and a price chart off Yahoo in the same video are two sources with no
  reconciliation between them.
- The waterfall's manual-row path is reachable only through the API. It is what the
  offline tests draw from and what a segment breakdown would use, but the interface has
  no editor for it — the bars chart's row editor is a two-column one and a bridge row
  needs a third field for its kind.

## Roadmap

Near term, in rough priority order.

1. Make `clean_config()` match its own contract: validate theme, aspect, easing and
   metric, and bound `duration`. Mostly a matter of calling `_one_of()`, which already
   does this for the three camera fields — the work is deciding what a bad theme should
   do to an API caller who has been getting Midnight, not writing the check.
2. Batch render — one config, many tickers, queued. The render subprocess is the piece
   that was missing; the queue already handles the rest.
3. Frame-drawing speed — the only lever that actually shortens a render. See the first
   known rough edge for where the time really goes.
4. Reload a past render's config from the queue. Jobs already carry their `cfg`; the UI
   just can't reach it, so "same chart, but AMD" means retyping everything.
5. Benchmark overlay: draw SPY muted behind any single-ticker chart.
6. Adjusted vs raw closes as an explicit choice. `auto_adjust=True` is hardcoded in
   `_yahoo`, and Yahoo and Stooq adjust differently enough to change the total return
   being narrated — see the note in the README.

Done since this list was last rewritten: the Stooq fallback, renders out of process,
intraday intervals, date-range presets, camera moves, log price axes with moving
averages, the encoder preset (`final` moved from `slow` to `medium`, with an
Auto/Faster/Slower override in the UI), brand kits, the landing page with email
capture — step 3 of docs/acquisition.md's sequencing, which leaves the demo instance
above it as a deploy rather than a code change — the licensed price feed, which was the
one hard blocker in front of charging anybody, symbol suggestions in the ticker field
with `/api/series` behind them, economic series from FRED behind that same field, the
timeline's automatic callouts — earnings, splits and dividends looked up per kind through
`data.events()`, laid out by `plan_callouts()`, and merged with the typed ones rather than
replacing them — and the revenue waterfall, the first chart here drawn from something
other than prices, which is what `fundamentals.py` exists for.

Two things the waterfall opens up that are not on the list above because they are one
fetcher each now rather than a module: a `pe` or `margin` metric on the bar chart, which
wants the same statements plus a price, and segment revenue, which is an FMP endpoint
Yahoo has no equivalent for — so it would be the first thing here that needs a key rather
than merely preferring one. A composition donut is the other obvious neighbour and the
one to be careful with: slices have to be parts of a whole, so revenue by segment or index
weights qualify and a chart of P/E ratios across tickers does not — those don't sum to
anything, and a pie of them would be exactly the wrong-but-looks-right failure the source
rules exist to prevent. That is a bar chart.

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

This is being explored as a product, and **`docs/pricing.md` is the decided plan** —
three paid tiers, no free tier and no watermarking, both of which were considered and
dropped. It supersedes the "watermarking, render credits" sketch this section used to
carry, and the $40 cinematography tier above is pencilled against it rather than in it.
`docs/acquisition.md` covers how anyone arrives; neither is enforced anywhere in the code.
Beyond the tiers, the last piece is an API endpoint that accepts a config and returns an
MP4.
**The licensed feed this used to be blocked on has shipped.** Two of them are in `data.py`,
either inert without its key: FMP answers first (`ROLLTAPE_FMP_KEY`), Twelve Data second.
FMP was chosen for its interval grid and for intraday history reaching back years rather
than months; Twelve Data stays in the order as the fallback and as the answer if FMP's
display licence turns out badly. $29/month either way — a fixed cost rather than a per-user
one, so roughly one cinematography subscriber covers it and everything past that is compute
and margin.

Two live caveats sit in Known rough edges rather than here: FMP Starter's five-year horizon,
and the display licence FMP's individual plans do not grant.

What is *not* automatic: the scraped sources are still in the order below it, because a
local install should keep working without an account. A deploy that takes money must set
`ROLLTAPE_LICENSED_ONLY=1`, which removes them — otherwise a quota exhausted mid-month
falls through to yfinance and puts the licensing question straight back.

## Style

Python: standard library plus the four that do the work — Flask, matplotlib, numpy,
pandas — with yfinance and imageio-ffmpeg alongside them as the two the app degrades
gracefully without. No frameworks beyond Flask. Comments explain *why*, not *what* — the
existing comments are the reference for tone. Keep functions flat and readable over
clever.

The same standard applies to tests: `unittest`, no pytest, no fixtures library, and a
docstring at the top of each module saying what it covers and what it deliberately
doesn't touch (network, ffmpeg, a real presets file). Run the suite before you call
anything done.

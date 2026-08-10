# Rolltape

Ticker in, animated chart video out. A local app for turning tickers into
animated chart videos for YouTube. Pick a chart type, type
symbols, watch the preview update, render an MP4.

## Setup

```bash
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000.

ffmpeg does the encoding, and `pip install` brings its own copy — there is nothing extra
to set up. If you already have ffmpeg on your PATH it gets used instead, which is the
leaner option: the bundled build adds about 77MB to the install.

Prices come from a real feed and only from a real feed — there is no generated-data mode.
Out of the box that means Yahoo with a Stooq fallback, which needs no account. Set
`ROLLTAPE_FMP_KEY` and Financial Modeling Prep answers first instead; see **Where the
numbers come from** for why you would, and for the one thing its entry plan can't do.

## Chart types

| Type | What it does | Tickers |
|---|---|---|
| Line reveal | One ticker drawing left to right, live price readout | 1 |
| Comparison | Several tickers indexed to 100, labels at the line ends | up to 6 |
| Candlesticks | OHLC candles appearing in sequence over a volume strip | 1 |
| Bar comparison | Bars growing to a metric or your own numbers | up to 8 |
| Annotated timeline | Line reveal with callouts landing on dates you set | 1 |
| Bar race | Ranked bars reordering as performance changes | up to 8 |
| Revenue waterfall | An income statement stepping down to net income | 1 |

Bar comparison can pull total return, max drawdown, annualised volatility or latest
close — or switch it to **My own numbers** and type revenue, margins, whatever you're
narrating.

## The revenue waterfall

The one chart here that isn't drawn from prices. It reads an income statement and steps
through it: revenue, then cost of revenue, gross profit, R&D and SG&A, operating income,
tax and everything else, landing on net income — each bar hanging off wherever the last one
finished, with the subtotals restated as full-height pillars and their share of revenue
printed above them.

Two bridges, on the **Bridge** control:

- **Revenue to net income** reads the latest filing and shows where the money went.
- **Revenue growth** reads a run of them and shows what each period added or gave back,
  opening on the earliest revenue and closing on the latest.

Pick annual or quarterly statements, and for the growth bridge how many periods it spans.
The date range above it disappears for this chart, because a filing is not a window you
drag — a waterfall is one period or a run of them, and nothing about 1Y or MAX changes what
it draws.

Everything on it is arithmetic between figures the company reported. Each change bar is one
subtotal subtracted from another rather than a sum of cost lines, so the bars land on the
net income the last pillar names even when a filer leaves a line out or classifies
something unusually — and a stage whose inputs are missing is dropped rather than guessed
at, so a company that reports no gross profit gets one operating-expenses bar instead of
three invented ones.

Statements come from Financial Modeling Prep when `ROLLTAPE_FMP_KEY` is set, and from
Yahoo otherwise. There is nothing behind Yahoo — Stooq publishes no fundamentals and Twelve
Data's are on a plan above the one wired here — so unlike a price chart, a waterfall fails
rather than falling through when both are down. Under `ROLLTAPE_LICENSED_ONLY` only FMP is
allowed, same as for prices.

The API also takes a bridge typed out by hand, for numbers no feed has — post `rows` of
`{"label", "value", "kind"}` where `kind` is `start`, `delta` or `total`, and the ticker
becomes optional. The interface doesn't expose that; it is there so a segment breakdown or
a figure off a slide can still be animated.

## Typing a ticker

Start typing and suggestions drop down under the field — symbol, company, exchange. Arrow
keys move through them, Enter or Tab takes one, Escape puts the list away, and a click does
the obvious thing. On a multi-ticker chart it completes only the symbol your cursor is in,
so adding a fourth name to a comparison won't disturb the first three.

It searches company names as well as symbols, which is the point: `bitcoin` finds
`BTC-USD` and `S&P 500` finds `^GSPC`, and neither is a ticker anyone types from memory.

Underneath, each symbol shows what actually came back for the range you picked — last
close, the move over the window, how many bars, and the source if it wasn't Yahoo. That
lands well before the preview finishes drawing, so a fat-fingered symbol or an empty range
shows up straight away instead of at the end of a render. Change the range and the numbers
follow it.

Two things worth knowing. Suggestions come from Yahoo's search with a built-in list of
common symbols underneath, so the field still works offline and when Yahoo
is having a bad day — you may just have to type an unusual symbol in full. And a symbol
that doesn't resolve says so on its own line; the others still load.

## Date range

One row of buttons: **1D, 1W, 1M, 3M, 6M, YTD, 1Y, 3Y, 5Y, 10Y, MAX**. Pick one and the
line under it spells out the window you just chose, so there's no guessing what "3M" ended
up meaning. Everything except 1D runs up to the latest close.

**1D** is the most recent trading session drawn from 5-minute bars — Friday's session if
you're working on a Sunday, so it's the one to reach for on a Fed day or an earnings gap.

**Custom** is the last button and opens the two date fields, which is where the old start
and end pickers live. Leave the end date empty to run up to the latest bar. The dates you
type stay put while you try presets, so you can flick back to Custom without retyping them.
Custom is also where **Bar interval** appears — every other preset names its own.

Labels follow the range on their own — a session gets a clock along the bottom, a week gets
days, a decade gets years — so nothing needs adjusting after switching.

## Bar interval

Daily by default. **Bar interval** also offers 1 minute, 5, 15, 30 and 1 hour, which is
what you want for a Fed afternoon or an earnings gap — the move is inside a single session,
so a daily chart shows it as one candle.

Intraday history is kept for a while and then dropped, by a different amount per interval:
about a week of 1-minute bars, two months of 5- to 30-minute, two years of hourly. A start
date earlier than that is pulled forward to whatever exists rather than refused, and the
subtitle names the range actually drawn.

Intraday charts are plotted by bar rather than by clock time, so the overnight hours are
closed up and the ticks land on the session opens. Without that, a week of 5-minute bars
would be about two thirds empty.

**Intraday never falls back to Stooq.** Stooq serves daily bars and coarser, so an
intraday render fails rather than quietly handing you daily bars with a 5-minute label. It
needs either a Twelve Data key or yfinance installed; with neither, the intraday options
are hidden rather than offered and broken.

## Price axis and moving averages

**Price axis** switches the line, comparison, candlestick and timeline charts between
linear and log. On a log axis equal percentage moves are equal heights, which is what you
want for anything spanning more than a year or two — a linear axis makes the recent end of
a long chart look far more dramatic than it was. When the default subtitle is in use it
says `log scale`, so nobody watching has to guess.

**Moving averages** takes up to three windows in trading days — `50, 200` is the usual
pair. They draw underneath the price line, animate along with it, and get a small key in
whichever corner the price leaves empty.

The averages are warmed up properly: Rolltape fetches extra history from before your start
date so a 200-day line is already at full value on the first bar drawn, rather than
appearing two thirds of the way across. On a candlestick chart that has rolled up to
weekly or monthly bars, a "50-day" average is still fifty *days* — it's computed on daily
closes before the rollup, not fifty candles.

**Average lag** holds the averages a beat behind the price line while it draws, so a
lagging indicator looks like one: the price moves and the average answers. Subtle, Standard
and Bold are how far behind — a fraction of a second up to about half of one — and the gap
closes as the reveal ends, so the last frame is the same finished chart either way. Off by
default, and the control only appears once you've asked for an average.

## Output settings

- **Frame** — 16:9 for the main video, 9:16 for Shorts, 1:1 for square posts.
- **Quality** — Draft is 720p30 and renders in seconds, good for checking motion.
  Final is 1080p60. Max is 1440p60.
- **Theme** — Midnight, Carbon, Paper and Terminal. Edit the `THEMES` dict at the top
  of `renderers.py` to add your channel's colours.
- **Background** — Solid paints the theme colour and writes an H.264 MP4. Transparent
  drops the backdrop entirely and writes ProRes 4444 in a `.mov`.

The slate in the top bar always shows exactly what you're about to produce: resolution,
frame rate, frame count, running time and codec.

## Overlays

Set **Background** to Transparent and the chart comes out with a real alpha channel —
drop it on a timeline over your talking-head footage and only the line, labels and grid
are drawn. No keying, no mattes, no luma tricks.

The format is ProRes 4444 in a `.mov`, because that is what every editor ingests without
a transcode. It is an intermediate rather than a delivery format, so expect large files —
a few hundred MB for a 1080p60 clip is normal. The preview shows a checkerboard behind the
chart so you can see what is actually transparent before you commit to the render.

## Thumbnails

**Save this frame** writes the frame you are previewing as a PNG at the full output
resolution — the same chart, the same moment, the same size as the video. Drag the scrub
bar to the point you want, hit the button, and that is your thumbnail. With Background set
to Transparent you get a transparent PNG instead, ready to composite.

The resolution and frame rate in the slate are clickable. Resolution cycles 720p, 1080p
and 1440p; frame rate toggles 30 and 60. Either can be changed on its own, so a Final
render at 30fps keeps CRF 16 rather than dropping to Draft's compression. A quality tier
sets both back to its own pairing, which is how you undo an override.

## Timing

Reveal length plus hold equals total video length. The hold freezes the finished chart
so you have room to talk over it before cutting away. Six seconds of reveal and one and
a half of hold is a sensible default; shorten the reveal for a punchier cut.

Easing controls how the reveal decelerates. **Ease out** starts fast and settles — the
right choice most of the time. **Both ends** eases in and out, which suits slow
atmospheric shots. **Linear** is for bar races, where constant speed reads as elapsed
time.

## Camera

Left alone, a chart draws into a fixed frame: the whole date range is on screen from the
first frame to the last. That is why the opening second of a reveal is mostly empty space
and the closing second is too wide to read a number off. **Move** animates the frame
instead.

- **Locked off** — the fixed frame, and the default. Nothing moves.
- **Pull back** — opens tight on the first few days and widens as the line arrives,
  reaching the full range before the reveal ends. The fix for an empty opening frame.
- **Follow** — a window travelling with the line, like a terminal replay. On the hold it
  settles back to the whole chart, so the clip still ends on the full picture.
- **Push in** — opens on the full range and dollies in, landing tight on the closing move
  exactly as the reveal ends.

**Travel** is how close it gets — Subtle, Standard or Bold. **Vertical** decides whether
the price scale moves with the frame. *Track* fills the frame as the camera moves and is
the more dramatic of the two. *Hold scale* keeps one price scale for the whole clip, so a
2% wobble stays visibly smaller than a 40% run — worth choosing when you are narrating the
size of a move rather than its shape.

Bar comparison and the bar race have no camera. The rows are the composition, and there is
no plane to move over, so the controls disappear for those two.

## Files

```
app.py          Flask server, render queue, job tracking
render_job.py   Runs one render in a child process, and reports progress back
renderers.py    All seven chart types, themes, easing, camera moves, export
data.py         FMP, Twelve Data, Yahoo and Stooq price fetches, in order, the disk
                cache, and the symbol search
fundamentals.py Income statements from FMP and Yahoo, and the waterfall's bridges
config.py       Env-var configuration, all defaulting to the local setup
storage.py      Where finished MP4s go
jobs.py         The render job registry
examples.py     The three charts the landing page shows
signups.py      Email capture, to a list provider or a local file
templates/      The interface, the landing page, and the pricing page at /pricing
outputs/        Rendered MP4s land here
```

Income statements are cached in the same directory and stamped with the day they were
fetched, since "the last five filings" changes the morning a company reports.

Price data is cached in `.cache/` so repeated renders of the same range don't re-download.
A finished historical window is cached for good — it can't change. A range with **End**
left empty runs to the latest bar, so it's cached only for the day it was fetched and
refreshes on the next render the following day. You get today's close without thinking
about it, and previews still don't re-download on every keystroke. Intraday goes finer
still and expires at the bar interval, because its last bar is the one that keeps moving.

**Clear price cache** in the interface wipes the lot, for when you want a source to be
re-asked immediately.

## Where the numbers come from

Four sources, tried in order.

1. **[Financial Modeling Prep](https://site.financialmodelingprep.com)** — the licensed
   feed, used whenever `ROLLTAPE_FMP_KEY` is set.
2. **[Twelve Data](https://twelvedata.com)** — the other licensed option, used whenever
   `ROLLTAPE_TWELVEDATA_KEY` is set.
3. **Yahoo**, via yfinance.
4. **[Stooq](https://stooq.com)** — free daily bars, no account, a completely independent
   source.

Each licensed source is inert without its key, so a fresh clone works with no account and
which feeds are live is a matter of configuration rather than code. Each source below the
last exists because the one above it breaks: Yahoo whenever they change their endpoints, a
licensed feed whenever a monthly quota runs out. A failed render is worse than one drawn
from second choice. If they all fail you get one error naming every cause.

The exception is intraday. Stooq has daily bars and nothing finer, so it is never asked for
5-minute data — answering with daily bars under a 5-minute label is worse than failing.

### Why FMP

- **Its interval grid is the one the interface already offers.**
  1min/5min/15min/30min/1hour map straight onto Rolltape's.
- **Its intraday history goes back years, not months.** This is the thing it does better
  than anything else at the price: a 5-minute chart of a specific afternoon two years ago
  is a render Twelve Data cannot serve at any tier.
- **Plain REST/JSON**, which suits a codebase that is standard library plus four packages.

### What the Starter plan can't do

**Starter reaches back five years.** The 10Y and MAX presets ask for more, and FMP answers
a too-long window with a short frame rather than an error — which would put five years of
history under a MAX label and look entirely correct.

Rolltape refuses to do that. `data.py` knows the plan's horizon and drops FMP for any
window that reaches past it, so a MAX chart falls through to Yahoo and draws in full. The
footer then names Yahoo rather than FMP, which is the honest answer. Under
`ROLLTAPE_LICENSED_ONLY=1` there is nothing to fall through to and the render fails with a
message naming the horizon.

**A moving average eats into that five years.** Averages are fetched warm, which means
pulling history from before the chart's left edge — 314 days for a 200-day line, 89 for a
50-day. That run-up counts against the horizon, so the deepest chart FMP will serve *with a
200-day average* starts about 4.1 years back rather than 5. Past that it falls through to
Yahoo and still draws correctly; it just stops being a licensed render.

If you upgrade to a plan with deeper history, set `ROLLTAPE_FMP_HISTORY_YEARS` to match —
that is the whole change, no code edit.

**One thing to settle before charging anyone.** FMP's individual plans do not cover
displaying data to end users or the public; that needs their Data Display and Licensing
Agreement, quoted rather than listed. Every render Rolltape produces is a public display,
so this applies to a YouTube upload as much as to a paid tier. Ask them before the first
paying user. `ROLLTAPE_TWELVEDATA_KEY` is wired and tested as the alternative if the answer
is unattractive — Twelve Data states display use is included in its $29 plan.

**The footer tells you which source answered.** Yahoo says nothing, since that's the
assumed source and there is nobody to credit. The others name themselves, and a render that
mixed sources names all of them — one ticker off a different feed than the rest is exactly
when a single label would be a lie.

That matters because the sources adjust prices differently: yfinance is asked for split-
*and* dividend-adjusted closes, Stooq adjusts on its own terms, and how the licensed feeds
do it has not been checked against either. The same ticker over the same window can show a
different total return depending on which one answered. Check the footer before you narrate
a number.

### Paying customers

Set `ROLLTAPE_LICENSED_ONLY=1` and the scraped sources are removed from the order
altogether: no key, or a licensed feed that is down, means a failed render rather than a
chart quietly drawn from data that may not be shown to someone who paid for it. Off by
default, because a laptop rendering for its owner is the case the fallbacks exist for.

The suggestion field is the one place with no fallback worth the name. None of the price
sources publishes a symbol search, so when Yahoo's is down the dropdown falls back to a
built-in list of common symbols. Anything outside it has to be typed in full — it still
charts, it just stops suggesting itself.

If you'd rather drive it yourself than use the field, both halves are plain HTTP:

```bash
curl 'http://127.0.0.1:5000/api/search?q=nvid'
curl -X POST http://127.0.0.1:5000/api/series \
     -H 'content-type: application/json' \
     -d '{"chart":"compare","tickers":["NVDA","AMD"],"range":"1y"}'
```

`/api/series` takes the same body as a render and hands back the closes and a summary per
ticker instead of a video, so it's also the quickest way to check what a config resolves to
before queueing a 90-second encode. Add `?points=N` to cap how many closes come back.

Run the tests with `python -m unittest`. They mock every source, so they need no network —
and the generated prices they draw from live in `testsupport.py`, which nothing the app
runs imports. There is no path from a running Rolltape to an invented number.

## The landing page

There is a public page at `/landing` — what the tool is, the three chart types it shows
best, and an email field. It is off the path of a local run: `/` stays the app, and you
only ever see the page if you go looking for it.

Set `ROLLTAPE_LANDING=1` and the two swap. `/` serves the landing page and the app moves
to `/app`, which is the arrangement a public instance wants. Both URLs work either way, so
a link you hand out keeps working if you change your mind.

The showcase frames are drawn by the renderer itself — `examples.py` holds three configs
and the page asks for `/examples/<id>.png`, which draws the frame once and caches it. So
what a visitor sees is genuinely what the tool produces, and adding a chart type to
`CHARTS` puts it in the page's chart list without anyone editing HTML. If the price source
is down the frames 404 and the page explains the gap rather than breaking.

Drawing three frames takes a second or so, which the first visitor to a cold container
would otherwise pay in series. Move it to deploy time:

```bash
python3 scripts/make_examples.py            # pre-warm the stills
python3 scripts/make_examples.py --clips    # and an MP4 of each, ~1 min per clip
```

**Point `ROLLTAPE_SIGNUP_URL` at a list provider before sending anyone there.** Without
one, addresses append to `signups.jsonl`, which is right on a laptop and lossy in a
container that restarts — the file goes when the instance does. With one set, the address
is POSTed as `{"email": ..., "source": ...}` and nothing touches the disk. A `409` back
from the provider counts as success, because "already subscribed" is not a failure from
the visitor's side.

Putting it in public means putting real market data in public, so set `ROLLTAPE_FMP_KEY`
and `ROLLTAPE_LICENSED_ONLY=1` before pointing anyone at it. The
showcase frames on the page are drawn by the real renderer from the real feed, which is
the point of them.

## Deploying

Rolltape is a local tool and runs best that way — rendering is CPU-bound, so your own
machine is usually faster than a small cloud box, and it costs nothing. **Everything below
is optional; running locally needs none of it.**

The important thing to understand before hosting it: rendering happens entirely on the
server. The browser only displays a PNG preview and polls for progress. So wherever you
deploy it, that machine's CPU does ~70 seconds of work per render, billed to you.

The code is deployment-ready. These env vars all default to the local behaviour:

| Variable | Default | Purpose |
|---|---|---|
| `ROLLTAPE_OUT_DIR` | `./outputs` | Where MP4s are written |
| `ROLLTAPE_CACHE_DIR` | `./.cache` | Where the price cache lives |
| `ROLLTAPE_FMP_KEY` | unset | Financial Modeling Prep API key; answers first when set |
| `ROLLTAPE_FMP_HISTORY_YEARS` | `5` | How far back the FMP plan reaches — Starter is 5, Professional 30 |
| `ROLLTAPE_TWELVEDATA_KEY` | unset | Twelve Data API key; the second licensed source |
| `ROLLTAPE_LICENSED_ONLY` | off | Refuse the Yahoo/Stooq fallbacks — for a deploy serving customers |
| `ROLLTAPE_LANDING` | off | Landing page at `/`, app at `/app` |
| `ROLLTAPE_DEMO_URL` | `/app` | Where the landing page's buttons point |
| `ROLLTAPE_SIGNUP_URL` | unset | List provider to POST signups to |
| `ROLLTAPE_SIGNUPS` | `./signups.jsonl` | Where signups go without a provider |
| `ROLLTAPE_EXAMPLES_DIR` | `./.examples` | Cached showcase frames |

### A container host

Railway, Render, Cloud Run or a plain VM. The `Dockerfile` covers all of them: it installs
ffmpeg with apt and serves the app under gunicorn. This is the only deployment shape the
app supports, and the one it was written for — a long-lived process with a real filesystem.

```bash
docker build -t rolltape .
docker run -p 5000:5000 -v rolltape-data:/data rolltape
```

On Railway, point it at the repo and it picks up the `Dockerfile` on its own. Attach a
volume mounted at `/data` so renders and the price cache survive a restart; the image
already defaults `ROLLTAPE_OUT_DIR` and `ROLLTAPE_CACHE_DIR` there. Railway supplies `PORT`.

**Do not raise gunicorn above `--workers 1`.** Job state lives in one process's memory, so
a second worker gives you a second job registry and renders that disappear from the UI.
Threads, not workers, handle the progress polling — and renders are already out of the web
process anyway, so extra workers buy nothing.

Sizing: the server process sits around 100 MB and stays there during a render. The render
itself runs in a child that comes and goes, so what the host has to fit is the server plus
one render, not one process doing both. It's CPU rather than memory that decides how fast a
clip renders.

Serverless is not supported. It was tried against Vercel and removed: the render is
queued and answered asynchronously, which a platform that freezes the instance the moment
the response is sent cannot carry — and the function bundle only fit its size ceiling by
about 3 MB, with yfinance dropped to get there.

**Before anything ships commercially:** set a licensed key and `ROLLTAPE_LICENSED_ONLY=1`.
yfinance scrapes Yahoo, and showing that data to paying users isn't permitted — the
licensed feeds are in place for exactly this, but the fallbacks are on by default and have
to be turned off deliberately. See **What the Starter plan can't do** for the display
licence question that is still open with FMP.

## Notes

Renders run one at a time, each in its own process, so you can queue several and carry on
adjusting the chart — the preview keeps answering while a render is going. Progress shows
per-frame in the Renders list. A render that dies, whether it hits a memory limit or ffmpeg
falls over, takes down only its own process and reports why in that list.

Fonts fall back gracefully, but installing Inter and JetBrains Mono will make the
output match the previews exactly.

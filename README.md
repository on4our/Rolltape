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

To poke at the interface without hitting Yahoo, run `python app.py --demo`. That swaps
in generated price data so every control still works offline.

## Chart types

| Type | What it does | Tickers |
|---|---|---|
| Line reveal | One ticker drawing left to right, live price readout | 1 |
| Comparison | Several tickers indexed to 100, labels at the line ends | up to 6 |
| Candlesticks | OHLC candles appearing in sequence over a volume strip | 1 |
| Bar comparison | Bars growing to a metric or your own numbers | up to 8 |
| Annotated timeline | Line reveal with callouts landing on dates you set | 1 |
| Bar race | Ranked bars reordering as performance changes | up to 8 |

Bar comparison can pull total return, max drawdown, annualised volatility or latest
close — or switch it to **My own numbers** and type revenue, margins, whatever you're
narrating.

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

Yahoo only keeps intraday history for a while, and by a different amount per interval:
about a week of 1-minute bars, two months of 5- to 30-minute, two years of hourly. A start
date earlier than that is pulled forward to whatever exists rather than refused, and the
subtitle names the range actually drawn.

Intraday charts are plotted by bar rather than by clock time, so the overnight hours are
closed up and the ticks land on the session opens. Without that, a week of 5-minute bars
would be about two thirds empty.

**Intraday needs Yahoo.** Stooq serves daily bars and coarser, so unlike a daily render
there is no fallback — if Yahoo is down, an intraday render fails rather than quietly
handing you daily bars with a 5-minute label. It also needs yfinance installed; without it
the intraday options are hidden rather than offered and broken.

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
renderers.py    All six chart types, themes, easing, camera moves, export
data.py         Yahoo fetch, Stooq fallback, disk cache, plus the demo generator
config.py       Env-var configuration, all defaulting to the local setup
storage.py      Where finished MP4s go
jobs.py         The render job registry
templates/      The interface
outputs/        Rendered MP4s land here
```

Price data is cached in `.cache/` so repeated renders of the same range don't re-download.
A finished historical window is cached for good — it can't change. A range with **End**
left empty runs to the latest bar, so it's cached only for the day it was fetched and
refreshes on the next render the following day. You get today's close without thinking
about it, and previews still don't re-download on every keystroke. Intraday goes finer
still and expires at the bar interval, because its last bar is the one that keeps moving.

**Clear price cache** in the interface wipes the lot, for when you want a source to be
re-asked immediately.

## Where the numbers come from

Yahoo first, via yfinance. Yahoo breaks periodically when they change their endpoints, so
when that happens Rolltape falls back to [Stooq](https://stooq.com) — free daily bars, no
account, a completely independent source. A failed render is worse than one drawn from
second choice. If both fail you get one error naming both causes.

The exception is the **1D** range. Stooq has daily bars and nothing finer, so there is no
second source for intraday — those renders either come from Yahoo or fail.

**The footer tells you which source answered.** Yahoo says nothing, since that's the
assumed source. A chart built from the fallback reads `Data: Stooq`, and one built from
`--demo` reads `Demo data` — which also stops generated prices reaching a video by accident.

This matters because the two sources adjust prices differently: yfinance is asked for
split- *and* dividend-adjusted closes, Stooq adjusts on its own terms. The same ticker over
the same window can show a different total return depending on which one answered. Check the
footer before you narrate a number.

Stooq is a reliability fallback for personal use, and a daily-bars one — see **Bar
interval** for what that means when Yahoo is down. It does not change the licensing
position for a paid tier — see CLAUDE.md.

Run the tests with `python -m unittest`. They mock both sources, so they need no network.

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
| `ROLLTAPE_DEMO` | off | Same as `--demo`, for hosts with no CLI |

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

**Still open before anything ships commercially:** yfinance scrapes Yahoo, and
redistributing that data to paying users isn't permitted. A licensed feed has to replace it
first — see CLAUDE.md.

## Notes

Renders run one at a time, each in its own process, so you can queue several and carry on
adjusting the chart — the preview keeps answering while a render is going. Progress shows
per-frame in the Renders list. A render that dies, whether it hits a memory limit or ffmpeg
falls over, takes down only its own process and reports why in that list.

Fonts fall back gracefully, but installing Inter and JetBrains Mono will make the
output match the previews exactly.

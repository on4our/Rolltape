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

## Files

```
app.py          Flask server, render queue, job tracking
render_job.py   Runs one render in a child process, and reports progress back
renderers.py    All six chart types, themes, easing, export
data.py         Yahoo fetch, Stooq fallback, disk cache, plus the demo generator
config.py       Env-var configuration, all defaulting to the local setup
storage.py      Where finished MP4s go
jobs.py         The render job registry
templates/      The interface
outputs/        Rendered MP4s land here
```

Price data is cached in `.cache/` so repeated renders of the same range don't re-download.
**Clear price cache** in the interface wipes it when you want fresh numbers.

## Where the numbers come from

Yahoo first, via yfinance. Yahoo breaks periodically when they change their endpoints, so
when that happens Rolltape falls back to [Stooq](https://stooq.com) — free daily bars, no
account, a completely independent source. A failed render is worse than one drawn from
second choice. If both fail you get one error naming both causes.

**The footer tells you which source answered.** Yahoo says nothing, since that's the
assumed source. A chart built from the fallback reads `Data: Stooq`, and one built from
`--demo` reads `Demo data` — which also stops generated prices reaching a video by accident.

This matters because the two sources adjust prices differently: yfinance is asked for
split- *and* dividend-adjusted closes, Stooq adjusts on its own terms. The same ticker over
the same window can show a different total return depending on which one answered. Check the
footer before you narrate a number.

Stooq is a reliability fallback for personal use. It does not change the licensing position
for a paid tier — see CLAUDE.md.

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

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

ffmpeg has to be on your PATH — it does the encoding.
macOS: `brew install ffmpeg`. Windows: `winget install ffmpeg`, or grab a build from
gyan.dev and add the `bin` folder to PATH.

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

The slate in the top bar always shows exactly what you're about to produce: resolution,
frame rate, frame count and running time.

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
renderers.py    All six chart types, themes, easing, export
data.py         Yahoo fetch with disk cache, plus the demo generator
config.py       Env-var configuration, all defaulting to the local setup
storage.py      Where finished MP4s go — local disk or object storage
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
| `ROLLTAPE_STORAGE` | `local` | `local` or `blob` (Vercel Blob) |
| `ROLLTAPE_JOBS` | `memory` | Job registry backend |

For Vercel specifically: `vercel.json` and `api/index.py` are in place, and
`requirements-vercel.txt` adds a bundled ffmpeg binary since serverless runtimes ship
none. Set `ROLLTAPE_STORAGE=blob` and `BLOB_READ_WRITE_TOKEN`, because a serverless
filesystem is read-only apart from `/tmp` and doesn't persist.

**Two things are still open before a deploy is production-safe:**

1. **The job registry is process memory.** `ROLLTAPE_JOBS=memory` is the only backend
   implemented. Serverless instances don't share memory, so a `/api/jobs` poll can land
   on an instance that never saw the job. At single-user traffic this mostly works and
   fails silently when it doesn't — a shared KV backend behind the seam in `jobs.py`
   fixes it properly.
2. **The data source.** yfinance scrapes Yahoo, and redistributing that data to paying
   users isn't permitted. A licensed feed has to replace it before anything ships
   commercially — see CLAUDE.md.

## Notes

Renders run one at a time in a background thread, so you can queue several and keep
working. Progress shows per-frame in the Renders list.

Fonts fall back gracefully, but installing Inter and JetBrains Mono will make the
output match the previews exactly.

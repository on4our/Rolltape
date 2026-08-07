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

### A container host — recommended

Railway, Render, Cloud Run or a plain VM. The `Dockerfile` covers all of them: it installs
ffmpeg with apt and serves the app under gunicorn. Nothing else is needed, because this is
the shape the app was written for — one long-lived process with a real filesystem.

```bash
docker build -t rolltape .
docker run -p 5000:5000 -v rolltape-data:/data rolltape
```

On Railway, point it at the repo and it picks up the `Dockerfile` on its own. Attach a
volume mounted at `/data` so renders and the price cache survive a restart; the image
already defaults `ROLLTAPE_OUT_DIR` and `ROLLTAPE_CACHE_DIR` there. Railway supplies `PORT`.

**Do not raise gunicorn above `--workers 1`.** Job state lives in one process's memory and
`RENDER_LOCK` serialises matplotlib's global pyplot state. A second worker gives you a
second job registry and renders that disappear from the UI. Threads, not workers, handle
the progress polling.

A render peaks around 127 MB of RAM at 1080p, so a small instance is fine. It's CPU, not
memory, that decides how fast a clip renders.

### Vercel

Serverless costs noticeably more effort than the above, and the job-registry caveat at the
bottom of this section applies to it specifically. `vercel.json` and `api/index.py` are in
place. Four things in that setup are load-bearing, so don't drop them:

- `MPLCONFIGDIR` is set in `api/index.py` before anything imports matplotlib. matplotlib
  builds a font cache under `$HOME` on first import, which is read-only there — the crash
  surfaces as a generic `FUNCTION_INVOCATION_FAILED` with no useful detail.
- `scripts/trim-bundle.sh` cuts the install from ~253MB to ~222MB against a 225MB function
  limit. Vercel's build image has no `strip`, so the debug-symbol saving doesn't land and
  the margin is roughly 3MB. A dependency bump will likely break it.

- `installCommand` points at `requirements-vercel.txt`, which adds a bundled ffmpeg binary.
  Vercel installs from `requirements.txt` by default and would never see that file — without
  this, every render fails at the encode step.
- `includeFiles: templates/**` puts `index.html` in the function bundle. Without it the
  homepage 404s.
- `maxDuration: 300` covers a render that takes longer than the old default.

`config.py` detects Vercel via its `VERCEL` env var and moves the writable paths to `/tmp`,
so the cache doesn't try to write next to the read-only source.

You still have to set up storage yourself: create a Blob store in the Vercel dashboard
(which injects `BLOB_READ_WRITE_TOKEN`) and set `ROLLTAPE_STORAGE=blob`. `/tmp` doesn't
survive past an invocation, so without this a finished MP4 is gone before you can download it.

Expect a slow first request after idle — importing matplotlib and pandas is not quick.

**Two things are still open before a deploy is production-safe:**

1. **The job registry is process memory** — a serverless problem only. `ROLLTAPE_JOBS=memory`
   is the only backend implemented. Serverless instances don't share memory, so a
   `/api/jobs` poll can land on an instance that never saw the job; at single-user traffic
   this mostly works and fails silently when it doesn't. A shared KV backend behind the seam
   in `jobs.py` fixes it. On a container host the question doesn't arise — there's one
   process, which is what the design assumes.
2. **The data source.** yfinance scrapes Yahoo, and redistributing that data to paying
   users isn't permitted. A licensed feed has to replace it before anything ships
   commercially — see CLAUDE.md.

## Notes

Renders run one at a time in a background thread, so you can queue several and keep
working. Progress shows per-frame in the Renders list.

Fonts fall back gracefully, but installing Inter and JetBrains Mono will make the
output match the previews exactly.

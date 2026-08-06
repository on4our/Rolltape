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
templates/      The interface
outputs/        Rendered MP4s land here
```

Price data is cached in `.cache/` so repeated renders of the same range don't re-download.
**Clear price cache** in the interface wipes it when you want fresh numbers.

## Notes

Renders run one at a time in a background thread, so you can queue several and keep
working. Progress shows per-frame in the Renders list.

Fonts fall back gracefully, but installing Inter and JetBrains Mono will make the
output match the previews exactly.

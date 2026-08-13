# Sandisk (SNDK) — slideshow and animated charts

Two deliverables for one video segment, built from the same figures:

| | What it is |
|---|---|
| `SanDisk-SNDK-FY2026.pptx` | 12-slide deck, dark, with speaker notes on every slide |
| `renders/*.mp4` | Animated charts, rendered through Rolltape itself |

Both draw from `sndk_data.py`, so they cannot disagree with each other.

## Build

```bash
python sndk_data.py                        # reconcile the figures — do this first
python render_charts.py --quality final    # the animated charts
node build_deck.js                         # the deck
```

`build_deck.js` reads `data.json`, which is written from `sndk_data.py`:

```bash
python -c "import json,sndk_data as s; json.dump({k:getattr(s,k) for k in \
 ('COMPANY','QUARTERS','FY2026','FY2025_REVENUE','SEGMENTS_FY2026','SEGMENTS_Q4',\
  'GUIDANCE_Q1_FY27','PRICE','VALUATION','TECH')}, open('data.json','w'), indent=2)"
```

## What is in the deck

1. Title — revenue up 175%, stock down 30%
2. The company — spinoff, size, FY2026 headline figures
3. What the share price did — high, recent level, drawdown
4. Revenue quarter by quarter — the acceleration
5. Gross margin — 29.9% to 84.6%
6. Earnings per share — $0.75 to $43.97
7. Segment mix — Datacenter, Edge, Consumer
8. Why the numbers moved — demand, BiCS10, HBF
9. Guidance — Q1 FY2027
10. Valuation — forward P/E, net cash, targets
11. The argument — bull and bear from the same figures
12. Close — three numbers

Speaker notes carry the talking points and the figures behind each slide, including
the two places where you should be careful on air (the disputed 12 August close, and
guidance being an estimate rather than a result).

## The animated charts

Rendered here, from reported figures typed into the renderer's manual-row path — no
price feed needed:

| Clip | Chart | Cut it in at |
|---|---|---|
| `01-quarterly-revenue` | Bars | Slide 4 |
| `02-segment-mix` | Waterfall | Slide 7 |
| `03-gross-margin` | Bars | Slide 5 |

Only the segment chart is a bridge, and only because its three bars are parts of one
whole that sum to the reported year. Quarterly revenue and quarterly margin are each
four separate figures rather than components of a total, so they are drawn as plain
comparisons — a bridge between them would land on Q4 while looking like it was
accumulating toward the full year. Note that the bars chart always sorts by value, so
with revenue and margin both rising every quarter these read newest-first, top to
bottom.

**Not rendered here** — these need a daily price series for SNDK, which could not be
reached from the machine this was built on. Their configs are written to `configs/`,
already normalised through `clean_config()`, so they are ready to run:

| Clip | Chart |
|---|---|
| `10-price-line` | Line reveal, 1 year, 50/200-day averages, follow camera |
| `11-price-candles` | Candlesticks, 6 months |
| `12-price-timeline` | Timeline with earnings callouts looked up automatically |
| `13-vs-memory-peers` | SNDK against MU, WDC and STX, indexed to 100 |

On a machine that can reach a price source, one command renders all seven:

```bash
python render_charts.py --quality final --with-price
```

A clip whose feed is unreachable fails on its own and the rest still render. The
configs also POST straight to the API if you would rather drive it that way:

```bash
curl -X POST localhost:5000/api/render -H 'Content-Type: application/json' \
     -d @configs/10-price-line.json
```

## Where the figures come from

Everything in `sndk_data.py` is a reported figure from a Sandisk earnings release
unless its comment says otherwise. `python sndk_data.py` re-checks that they still
reconcile — quarters against the full year, segments against the total, and the
derived FY2025 base against both the headline growth rate and the per-segment rates.
It exits non-zero if any of that stops holding, and `render_charts.py` refuses to
render when it does.

Two things are deliberately absent:

- **A daily price series.** None could be sourced here, and a fabricated one animated
  as a real chart is the exact failure the renderer's own rules exist to prevent. Price
  appears only as individually-sourced milestones, and the one figure the sources
  disagreed on — the 12 August close — is printed as a range rather than to the cent.
- **GAAP cost and expense lines.** The published margins are non-GAAP; subtracting a
  non-GAAP margin from GAAP revenue would give a bridge that closes arithmetically and
  means nothing. Both bridges are built only from figures reported on the same basis.

## Re-running after the next earnings release

Add a dict to `QUARTERS`, update `FY2026`/`SEGMENTS_*`/`GUIDANCE_*`, run
`python sndk_data.py` until it reconciles, then rebuild. Nothing else is hard-coded —
the deck's charts, the ramp on the title slide and both bridges all derive from that
list.

## Tools

`qa_deck.py` checks the deck's geometry: shapes off-slide or inside the margin, text
boxes that overlap, and text too long for its box. `preview_deck.py` lays the finished
pptx out as HTML for a visual look — LibreOffice could not open a pptx in the container
this was built in, so the usual convert-to-images route was unavailable. Charts show as
labelled placeholders there; trust it for layout, not for the last pixel.

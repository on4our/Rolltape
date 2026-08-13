"""Render the SNDK animated charts through Rolltape's own renderers.

Two kinds of chart come out of this, and the split is about where the numbers come from
rather than about how they look:

- The three built here draw from *reported* figures typed into `rows`, which is the
  renderer's manual-bridge path. They need no price feed, so they render anywhere.
- The price charts (line, candlesticks, timeline) need a daily OHLCV series for SNDK.
  This script does not fetch one and does not fake one — it writes their configs to
  `configs/` so they can be run on a machine that can reach a price source. See README.md.

Configs go through `app.clean_config()` rather than straight into `renderers.render()`,
so what renders here is exactly what the UI or the API would produce from the same
settings — including the validation. The JSON written to `configs/` is post-clean for the
same reason: it can be POSTed to /api/render as-is.

    python render_charts.py                 # draft, fast
    python render_charts.py --quality final # what goes in the video
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import app                                        # noqa: E402
import renderers                                  # noqa: E402
import sndk_data as sndk                          # noqa: E402

OUT_DIR = os.path.join(HERE, "renders")
CFG_DIR = os.path.join(HERE, "configs")

# One look across every clip in the set. Midnight is the darkest of the four themes and
# the deck is built on the same background, so the slideshow and the animations cut
# together rather than flashing between two grounds.
LOOK = {"theme": "midnight", "aspect": "16:9", "easing": "cubic",
        "footer": "Source: Sandisk quarterly results, FY2026"}

MILLION = 1_000_000


def _bridge_rows(rows, scale=MILLION):
    """Reported $M figures as the renderer's row dicts, in whole dollars.

    The scale matters: `_compact` picks its suffix off the magnitude, so a figure left in
    millions prints as "$2.31K" where the same figure in dollars prints "$2.31B". The
    filing is in millions and the chart should read like the filing.
    """
    return [{"label": r["label"], "value": r["value"] * scale, "kind": r["kind"]}
            for r in rows]


def configs():
    """Every clip in the set, keyed by output filename."""
    out = {}

    # 1. The acceleration. Quarterly revenue from Q1 to Q4 of FY2026 — a bridge because
    # the story is the *steps*, not the four levels: two thirds of the year's growth
    # arrived in the last two quarters.
    out["01-revenue-build"] = dict(LOOK, **{
        "chart": "waterfall",
        "rows": _bridge_rows(sndk.revenue_bridge()),
        "title": "Sandisk quarterly revenue, FY2026",
        "subtitle": "Q1 to Q4 — the quarter got 3.9x bigger",
        "duration": 9.0, "hold": 2.0,
    })

    # 2. The mix. The three segments sum to the reported year, so this is a composition
    # rather than a bridge — legitimate for exactly that reason.
    out["02-segment-mix"] = dict(LOOK, **{
        "chart": "waterfall",
        "rows": _bridge_rows(
            [{"label": s["name"], "value": s["revenue"], "kind": "delta"}
             for s in sndk.SEGMENTS_FY2026]
            + [{"label": "FY2026 revenue", "value": sndk.FY2026["revenue"],
                "kind": "total"}]),
        "title": "Where the FY2026 revenue came from",
        "subtitle": "Datacenter grew 437% year over year",
        "duration": 8.0, "hold": 2.0,
    })

    # 3. The margin. Bars rather than a bridge: percentage points would add correctly, but
    # the waterfall formats its values as money and rounds 84.6 to 85 — losing the decimal
    # on the headline number of the whole year.
    out["03-gross-margin"] = dict(LOOK, **{
        "chart": "bars",
        "rows": [{"label": q["label"], "value": q["gross_margin"]}
                 for q in sndk.QUARTERS],
        "unit": "%", "decimals": 1,
        "title": "Non-GAAP gross margin by quarter",
        "subtitle": "FY2026: 29.9% to 84.6%",
        "duration": 7.0, "hold": 2.0,
    })

    return out


def price_configs():
    """The clips that need a daily price series, written out but not rendered here.

    Kept in this file rather than in the README so they stay valid: they go through the
    same `clean_config()` as everything above, so a field that stops being accepted fails
    here instead of on the user's machine.
    """
    look = dict(LOOK, footer="Source: SNDK daily closes")
    return {
        "10-price-line": dict(look, **{
            "chart": "line", "tickers": ["SNDK"], "range": "1y",
            "title": "SNDK", "subtitle": "The last twelve months",
            "camera": "follow", "duration": 12.0, "hold": 2.5, "ma": "50,200",
        }),
        "11-price-candles": dict(look, **{
            "chart": "candles", "tickers": ["SNDK"], "range": "6m",
            "title": "SNDK", "subtitle": "Six months of daily candles",
            "duration": 10.0, "hold": 2.0,
        }),
        # The callouts are the point here: earnings dates are looked up rather than typed,
        # so the marks land where the company actually reported.
        "12-price-timeline": dict(look, **{
            "chart": "timeline", "tickers": ["SNDK"], "range": "1y",
            "title": "SNDK — the year in context",
            "subtitle": "Earnings dates marked automatically",
            "auto_annotations": ["earnings"], "camera": "pullback",
            "duration": 13.0, "hold": 3.0,
        }),
        # Indexed to 100, so it compares the *shape* of the moves rather than the prices.
        "13-vs-memory-peers": dict(look, **{
            "chart": "compare", "tickers": ["SNDK", "MU", "WDC", "STX"], "range": "1y",
            "title": "SNDK against the memory complex",
            "subtitle": "Indexed to 100", "duration": 11.0, "hold": 2.5,
        }),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quality", default="draft",
                    choices=list(renderers.ENCODE), help="draft is fast; final is the one")
    ap.add_argument("--resolution", type=int, default=None, choices=renderers.RESOLUTIONS)
    ap.add_argument("--only", default=None, help="render just the clips matching this")
    ap.add_argument("--with-price", action="store_true",
                    help="also render the price charts — needs a reachable price source")
    args = ap.parse_args()

    problems = sndk.check()
    if problems:
        for line in problems:
            print("MISMATCH:", line)
        raise SystemExit("The figures do not reconcile — not rendering.")

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(CFG_DIR, exist_ok=True)

    todo = dict(configs())
    if args.with_price:
        todo.update(price_configs())
    else:
        for name, raw in price_configs().items():
            cfg = app.clean_config(dict(raw, quality=args.quality))
            with open(os.path.join(CFG_DIR, f"{name}.json"), "w") as fh:
                json.dump(cfg, fh, indent=2, default=str)
        print(f"wrote {len(price_configs())} price-chart configs to configs/ — pass "
              f"--with-price to render them too (needs a price source)")

    failed = []
    for name, raw in todo.items():
        if args.only and args.only not in name:
            continue
        settings = dict(raw, quality=args.quality)
        if args.resolution:
            settings["resolution"] = args.resolution
        cfg = app.clean_config(settings)
        with open(os.path.join(CFG_DIR, f"{name}.json"), "w") as fh:
            json.dump(cfg, fh, indent=2, default=str)

        path = os.path.join(OUT_DIR, name + renderers.output_extension(cfg["transparent"]))
        started = time.time()
        print(f"rendering {name} ({cfg['resolution']}p{cfg['fps']}, {args.quality}) ... ",
              end="", flush=True)
        try:
            renderers.render(cfg, path)
        except Exception as exc:
            # A price chart whose feed is unreachable should cost that clip and not the
            # run — the ones drawn from typed figures have nothing to do with it.
            print(f"FAILED — {exc}")
            failed.append(name)
            continue
        print(f"{time.time() - started:.0f}s, {os.path.getsize(path) / 1e6:.1f} MB")

    print(f"\nrenders in {OUT_DIR}")
    if failed:
        print(f"{len(failed)} did not render: {', '.join(failed)}")
        print("A price chart needs a source data.py can reach — see README.md.")


if __name__ == "__main__":
    main()

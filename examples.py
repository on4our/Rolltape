"""The three charts the landing page shows.

Real output or nothing. These are drawn by the same `renderers.save_still()` the preview
and the thumbnail export use, so what a visitor sees on the page is a frame the product
actually produced rather than a mockup of one. That constraint is the whole point: the
page is selling render quality, and a hand-made picture of render quality is a lie that
gets found out on the first render.

Stills rather than clips, and that is a real trade — the thing being sold is motion. The
reason is not file size, which turns out to be about half a megabyte per clip: it is that
a still can be drawn on request in a fifth of a second and stays current, where a clip
costs the best part of a minute and would either block the page or have to be committed,
frozen at whatever day it was rendered. `scripts/make_examples.py --clips` produces them
for when there is somewhere to host video.

Drawing is not free, so each one is written to disk the first time it is asked for and
served from there afterwards. Pre-warm them at deploy and no visitor ever pays for it.
"""

import os

import renderers

# Three because that is what docs/acquisition.md specifies, and these three because they
# span what the tool does: one ticker, several tickers, and a ticker with a story told over
# it. Each carries a different theme — the range of looks is a selling point and a grid of
# near-identical frames hides it.
#
# The bar race is deliberately not here, despite being the chart that travels furthest on
# its own. Frozen, it is a plain bar chart caught mid-reorder: rows sit between ranks and
# one runaway leader flattens the rest. Everything it is good at is motion, so a still of
# it undersells the tool rather than selling it. It wants a clip, which is what
# scripts/make_examples.py is for.
EXAMPLES = {
    "line": {
        "label": "Line reveal",
        "blurb": "One ticker drawing left to right with a live price readout, moving "
                 "averages already warm on the first bar.",
        # Locked off, not one of the camera moves. `still=` maps across the reveal only,
        # so a travelling camera is always caught mid-move here — fine in a video, dead
        # space on the right in a frame that has to stand alone.
        "at": 0.97,
        "cfg": {
            "chart": "line", "tickers": ["NVDA"], "range": "5y", "theme": "midnight",
            "ma": "50, 200", "camera": "locked",
            "title": "NVDA", "footer": "@yourchannel",
        },
    },
    "compare": {
        "label": "Comparison",
        "blurb": "Several tickers indexed to 100 so the lines start together, labelled at "
                 "the ends rather than in a legend.",
        "at": 0.95,
        "cfg": {
            "chart": "compare",
            "tickers": ["AAPL", "MSFT", "GOOGL", "AMZN", "META"],
            "range": "5y", "theme": "carbon", "normalize": True,
            # The subtitle already says "indexed to 100", so the title says something else.
            "title": "Big tech, five years", "footer": "@yourchannel",
        },
    },
    "timeline": {
        "label": "Annotated timeline",
        "blurb": "The same reveal with callouts landing on the dates they refer to, as "
                 "the line reaches them.",
        "at": 0.97,
        "cfg": {
            "chart": "timeline", "tickers": ["SPY"], "theme": "paper",
            "range": "custom", "start": "2020-01-01", "end": "2020-12-31",
            "title": "SPY, 2020", "footer": "@yourchannel",
            "annotations": [
                {"date": "2020-02-19", "label": "Pre-COVID peak"},
                {"date": "2020-03-23", "label": "Bottom"},
                {"date": "2020-08-18", "label": "Back to even"},
            ],
        },
    },
}

# Big enough to stay sharp on a retina display at the width the grid gives it, small
# enough that three of them aren't the page's whole weight.
STILL_RES = 720


def path_for(example_id, examples_dir):
    return os.path.join(examples_dir, f"{example_id}.png")


def write_still(example_id, cfg, examples_dir):
    """Draw one example and cache it. Returns the path.

    The caller supplies an already-cleaned `cfg` — validation lives in `clean_config()`
    and this module is not a second place for it.
    """
    path = path_for(example_id, examples_dir)
    os.makedirs(examples_dir, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as fh:
        renderers.save_still(cfg, fh, at=EXAMPLES[example_id]["at"], res=STILL_RES)
    # Same reason presets.py renames: a visitor arriving mid-draw should get a miss and
    # wait, not a half-written PNG cached forever.
    os.replace(tmp, path)
    return path

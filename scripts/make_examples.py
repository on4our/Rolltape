#!/usr/bin/env python3
"""Draw the landing page's showcase frames ahead of time.

    python3 scripts/make_examples.py            # the three stills
    python3 scripts/make_examples.py --clips    # and an MP4 of each

The page draws a missing frame on request and caches it, so this is not required —
it just moves the cost off the first visitor. On a cold container that visitor
otherwise waits for three renders in series behind DRAW_LOCK, which is a poor first
impression from a page whose whole argument is that the output looks good.

Run it as a deploy step, after the image is built and before traffic arrives.

`--clips` is the other half. The page ships stills because they redraw in a fifth of a
second and stay current, but the thing being sold is motion — so when there is somewhere
to host video, this is what produces it. Budget about a minute per clip; they land in the
configured output directory at roughly half a megabyte each, and nothing serves them
automatically.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402  — clean_config lives here and is the only validator
import config  # noqa: E402
import data as datasrc  # noqa: E402
import examples as showcase  # noqa: E402
import renderers  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--clips", action="store_true",
                    help="also encode each example as a video")
    ap.add_argument("--force", action="store_true",
                    help="redraw stills that are already cached")
    args = ap.parse_args()

    os.makedirs(config.EXAMPLES_DIR, exist_ok=True)
    failed = 0

    for example_id, spec in showcase.EXAMPLES.items():
        path = showcase.path_for(example_id, config.EXAMPLES_DIR)
        if os.path.exists(path) and not args.force:
            print(f"  {example_id:<10} still cached, skipping")
        else:
            started = time.time()
            try:
                cfg = app.clean_config(dict(spec["cfg"]))
                showcase.write_still(example_id, cfg, config.EXAMPLES_DIR)
            except Exception as exc:  # noqa: BLE001
                # Keep going: one dead example is a gap in the grid, and the page is
                # built to survive that. Stopping here would leave the others undrawn.
                print(f"  {example_id:<10} FAILED — {exc}")
                failed += 1
                continue
            size = os.path.getsize(path) / 1e3
            print(f"  {example_id:<10} still {size:>6.0f} kB  {time.time() - started:>5.1f}s")

        if not args.clips:
            continue

        started = time.time()
        try:
            cfg = app.clean_config(dict(spec["cfg"]))
            ext = renderers.output_extension(cfg["transparent"])
            out = os.path.join(config.OUT_DIR, f"example-{example_id}{ext}")
            os.makedirs(config.OUT_DIR, exist_ok=True)
            renderers.render(cfg, out)
        except Exception as exc:  # noqa: BLE001
            print(f"  {example_id:<10} FAILED clip — {exc}")
            failed += 1
            continue
        size = os.path.getsize(out) / 1e6
        print(f"  {example_id:<10} clip  {size:>6.1f} MB  {time.time() - started:>5.1f}s"
              f"  {out}")

    note = datasrc.attribution()
    if note:
        print(f"\n{note} — the frames are stamped to say so.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

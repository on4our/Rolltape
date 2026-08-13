"""Add a slide transition to every slide of the finished deck.

pptxgenjs has no API for transitions — they live in `<p:transition>` inside each slide
part, which it never writes — so this edits the package after the fact. Run it after
`node build_deck.js`; running it twice is harmless, since a slide that already carries a
transition is skipped rather than given a second one.

The edit is a string insertion rather than an XML round trip on purpose. Reserialising
OOXML through ElementTree rewrites namespace prefixes and corrupts the deck, and every
slide pptxgenjs writes ends in exactly the same way, so finding the insertion point is
not the hard part it would be on a hand-authored file.

Order inside `<p:sld>` is fixed by the schema: cSld, clrMapOvr, transition, timing,
extLst. So the transition goes immediately after `</p:clrMapOvr>` — which is also
immediately before `</p:sld>`, because pptxgenjs writes no timing tree.

    python add_transitions.py [deck.pptx] [--style fade|push|wipe|none]
"""

import argparse
import os
import re
import shutil
import sys
import zipfile

# Deliberately plain. A data deck that changes transition between slides reads as a
# template someone clicked through, and the point of these is to be almost unnoticed —
# they exist so a cut between two dark slides is not a hard jump on camera.
STYLES = {
    "fade": "<p:fade/>",
    "push": '<p:push dir="u"/>',
    "wipe": '<p:wipe dir="r"/>',
}

SLIDE = re.compile(r"^ppt/slides/slide\d+\.xml$")
ANCHOR = "</p:clrMapOvr>"


def transition_xml(style, speed="med"):
    return f'<p:transition spd="{speed}" advClick="1">{STYLES[style]}</p:transition>'


def apply(path, style, speed="med"):
    if style == "none":
        print("style=none — nothing to do")
        return 0

    src = zipfile.ZipFile(path)
    parts = [(i, src.read(i.filename)) for i in src.infolist()]
    src.close()

    xml = transition_xml(style, speed).encode()
    touched, skipped = 0, 0
    out = []
    for info, data in parts:
        if SLIDE.match(info.filename):
            if b"<p:transition" in data:
                skipped += 1
            elif ANCHOR.encode() in data:
                data = data.replace(ANCHOR.encode(), ANCHOR.encode() + xml, 1)
                touched += 1
            else:
                # Not a shape this script understands — leave it exactly as it was rather
                # than guess at an insertion point.
                print(f"  ! {info.filename}: no {ANCHOR}, left alone")
        out.append((info, data))

    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for info, data in out:
            # Keep each part's original compression so the package is otherwise identical.
            zf.writestr(info, data, compress_type=info.compress_type)
    shutil.move(tmp, path)

    print(f"{style} transition added to {touched} slide(s)"
          + (f", {skipped} already had one" if skipped else ""))
    return touched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("deck", nargs="?", default="SanDisk-SNDK-FY2026.pptx")
    ap.add_argument("--style", default="fade", choices=list(STYLES) + ["none"])
    ap.add_argument("--speed", default="med", choices=("slow", "med", "fast"))
    args = ap.parse_args()

    if not os.path.exists(args.deck):
        sys.exit(f"no such deck: {args.deck}")
    apply(args.deck, args.style, args.speed)


if __name__ == "__main__":
    main()

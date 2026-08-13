"""Post-process the built deck: slide transitions, and autoplay for the embedded clips.

Both live in parts of the slide XML that pptxgenjs does not write. Transitions are a
single `<p:transition>` element; autoplay is a `<p:timing>` tree, which is the fiddlier
half and the reason this file exists at all — without it PowerPoint treats an embedded
video as "start on click", and since each clip's poster is its own final frame, the slide
just looks like a static chart that will not move.

Run after `node build_deck.js`, which drops both (it rewrites the slide parts):

    python finish_deck.py                       # fade + autoplay
    python finish_deck.py --style push          # a different transition
    python finish_deck.py --no-autoplay         # leave the clips on click-to-play

Edits are string insertions rather than an XML round trip: reserialising OOXML through
ElementTree rewrites namespace prefixes and corrupts the deck, and every part pptxgenjs
writes ends the same way, so the insertion point is unambiguous.

Schema order inside `<p:sld>` is fixed — cSld, clrMapOvr, transition, timing, extLst —
so both go after `</p:clrMapOvr>`, transition first.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile

# Deliberately plain. A data deck that changes transition between slides reads as a
# template someone clicked through; these exist so a cut between two dark slides is not
# a hard jump on camera.
STYLES = {
    "fade": "<p:fade/>",
    "push": '<p:push dir="u"/>',
    "wipe": '<p:wipe dir="r"/>',
}

SLIDE = re.compile(r"^ppt/slides/slide\d+\.xml$")
ANCHOR = "</p:clrMapOvr>"
# The media shape's own id, read per slide rather than assumed — pptxgenjs numbers shapes
# per slide and a layout change would move it.
MEDIA_PIC = re.compile(r'<p:pic>(?:(?!</p:pic>).)*<a:videoFile', re.S)
CNVPR_ID = re.compile(r'<p:cNvPr id="(\d+)"')


def transition_xml(style, speed):
    return f'<p:transition spd="{speed}" advClick="1">{STYLES[style]}</p:transition>'


def timing_xml(spid, dur_ms):
    """A timing tree that plays one media shape as soon as the slide arrives.

    The shape of this is not free invention — it is the tree PowerPoint itself writes for
    a video set to Start: Automatically, reduced to the single effect this deck needs.
    Two details carry the behaviour:

    - The click group (`id="3"`) starts on `delay="0"` rather than the `indefinite` that
      a click-triggered group would carry. Indefinite is exactly the "waits for you to
      click" state this is here to remove.
    - The effect is `presetClass="mediacall"` with `cmd="playFrom(0.0)"`, which is the
      media-playback command rather than an entrance animation. The clip already contains
      its own motion; it only needs starting.

    `dur` is the clip's real length in milliseconds, so the node ends when the video does.
    """
    return (
        '<p:timing><p:tnLst><p:par>'
        '<p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot"><p:childTnLst>'
        '<p:seq concurrent="1" nextAc="seek">'
        '<p:cTn id="2" dur="indefinite" nodeType="mainSeq"><p:childTnLst>'
        '<p:par><p:cTn id="3" fill="hold">'
        '<p:stCondLst><p:cond delay="0"/></p:stCondLst>'
        '<p:childTnLst>'
        '<p:par><p:cTn id="4" fill="hold">'
        '<p:stCondLst><p:cond delay="0"/></p:stCondLst>'
        '<p:childTnLst>'
        '<p:par><p:cTn id="5" presetID="1" presetClass="mediacall" presetSubtype="0"'
        ' fill="hold" nodeType="afterEffect">'
        '<p:stCondLst><p:cond delay="0"/></p:stCondLst>'
        '<p:childTnLst>'
        '<p:cmd type="call" cmd="playFrom(0.0)"><p:cBhvr>'
        f'<p:cTn id="6" dur="{dur_ms}" fill="hold"/>'
        f'<p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl>'
        '</p:cBhvr></p:cmd>'
        '</p:childTnLst></p:cTn></p:par>'
        '</p:childTnLst></p:cTn></p:par>'
        '</p:childTnLst></p:cTn></p:par>'
        '</p:childTnLst></p:cTn>'
        '<p:prevCondLst><p:cond evt="onPrev" delay="0">'
        '<p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>'
        '<p:nextCondLst><p:cond evt="onNext" delay="0">'
        '<p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst>'
        '</p:seq></p:childTnLst></p:cTn></p:par></p:tnLst></p:timing>'
    )


def clip_ms(path):
    """Clip length in milliseconds, probed rather than assumed."""
    try:
        import imageio_ffmpeg
        out = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-i", path],
                             capture_output=True, text=True).stderr
        m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", out)
        if m:
            h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            return int((h * 3600 + mi * 60 + s) * 1000)
    except Exception:
        pass
    return 10000  # a sane fallback; the node ending early only stops the timing node


def media_spid(xml):
    """The shape id of this slide's video, or None if it has no video."""
    m = MEDIA_PIC.search(xml)
    if not m:
        return None
    ids = CNVPR_ID.search(m.group(0))
    return ids.group(1) if ids else None


def finish(path, style, speed, autoplay, media_dir):
    src = zipfile.ZipFile(path)
    parts = [(i, src.read(i.filename)) for i in src.infolist()]
    src.close()

    trans = transition_xml(style, speed).encode() if style != "none" else b""
    # One duration for any clip, taken from the longest — the media node only needs to
    # outlast the video, and reading each slide's own file back would mean matching
    # package parts to source files for no gain.
    longest = 10000
    if os.path.isdir(media_dir):
        lengths = [clip_ms(os.path.join(media_dir, f))
                   for f in os.listdir(media_dir) if f.endswith(".mp4")]
        if lengths:
            longest = max(lengths)

    out, n_trans, n_auto = [], 0, 0
    for info, data in parts:
        if SLIDE.match(info.filename) and ANCHOR.encode() in data:
            add = b""
            if trans and b"<p:transition" not in data:
                add += trans
                n_trans += 1
            if autoplay and b"<p:timing" not in data:
                spid = media_spid(data.decode())
                if spid:
                    add += timing_xml(spid, longest).encode()
                    n_auto += 1
            if add:
                data = data.replace(ANCHOR.encode(), ANCHOR.encode() + add, 1)
        out.append((info, data))

    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for info, data in out:
            zf.writestr(info, data, compress_type=info.compress_type)
    shutil.move(tmp, path)

    print(f"{style} transition on {n_trans} slide(s); autoplay on {n_auto} clip(s)")
    return n_trans, n_auto


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("deck", nargs="?", default="SanDisk-SNDK-FY2026.pptx")
    ap.add_argument("--style", default="fade", choices=list(STYLES) + ["none"])
    ap.add_argument("--speed", default="med", choices=("slow", "med", "fast"))
    ap.add_argument("--no-autoplay", action="store_true",
                    help="leave the embedded clips on click-to-play")
    args = ap.parse_args()

    if not os.path.exists(args.deck):
        sys.exit(f"no such deck: {args.deck}")
    finish(args.deck, args.style, args.speed, not args.no_autoplay,
           os.path.join(os.path.dirname(os.path.abspath(args.deck)), "renders"))


if __name__ == "__main__":
    main()

"""Animated chart renderers. Every chart type shares one theme, easing and export path."""

import logging
import shutil
from dataclasses import dataclass

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")

# When a font in the preference stacks isn't installed, matplotlib warns once per text
# object per frame — tens of thousands of lines for one render, enough that Railway's
# 500 logs/sec limit starts dropping messages. The stacks end in DejaVu, which ships
# with matplotlib, so the fallback always works and the warning is pure noise.
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.animation import FFMpegWriter, FuncAnimation
from matplotlib.collections import LineCollection, PolyCollection

import data as datasrc

# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------
THEMES = {
    "midnight": {
        "label": "Midnight",
        "bg": "#0B0E14", "grid": "#1E2430", "axis": "#2A3342",
        "text": "#E5E7EB", "muted": "#6B7280",
        "up": "#4ADE80", "down": "#F87171",
        "series": ["#60A5FA", "#4ADE80", "#F472B6", "#FBBF24", "#A78BFA", "#22D3EE"],
    },
    "carbon": {
        "label": "Carbon",
        "bg": "#111111", "grid": "#242424", "axis": "#333333",
        "text": "#F5F5F5", "muted": "#8A8A8A",
        "up": "#00E5A0", "down": "#FF5C5C",
        "series": ["#00E5A0", "#FF5C5C", "#FFC542", "#4C9AFF", "#C792EA", "#FF8A65"],
    },
    "paper": {
        "label": "Paper",
        "bg": "#FAF8F3", "grid": "#E4DFD4", "axis": "#C9C2B4",
        "text": "#1A1A1A", "muted": "#7A756B",
        "up": "#0F7B4F", "down": "#B3392B",
        "series": ["#1F4E79", "#0F7B4F", "#B3392B", "#B7791F", "#5B3A7E", "#0E7490"],
    },
    "terminal": {
        "label": "Terminal",
        "bg": "#050807", "grid": "#11221A", "axis": "#1C3A2B",
        "text": "#D8FFE6", "muted": "#4E7A62",
        "up": "#3DFF8F", "down": "#FF6B5B",
        "series": ["#3DFF8F", "#FFD166", "#5BC0EB", "#FF6B5B", "#C77DFF", "#00E0C7"],
    },
}

# Keyed by the short side — the number people say when they name a resolution. The aspect
# decides which side it lands on, so 720 is 1280x720 wide and 720x1280 tall.
SIZES = {
    "16:9": {720: (1280, 720), 1080: (1920, 1080), 1440: (2560, 1440)},
    "9:16": {720: (720, 1280), 1080: (1080, 1920), 1440: (1440, 2560)},
    "1:1": {720: (720, 720), 1080: (1080, 1080), 1440: (1440, 1440)},
}
RESOLUTIONS = (720, 1080, 1440)
FPS_CHOICES = (30, 60)

# "final" used preset=slow, but slow's extra reference/lookahead frames were getting
# ffmpeg OOM-killed on small container hosts (and took ~70s for a 7.5s clip). At CRF 16
# the visual difference from medium is not worth either cost. "max" keeps slow for the
# rare render where it matters — on a memory-limited host, that's the tier to avoid.
#
# "fps" and "res" are the tier's starting point, not a fixed property of it: the slate in
# the UI overrides either one, so a 1080p tier can be sent out at 30fps or 720p without
# giving up CRF 16. crf and preset stay tied to the tier.
ENCODE = {
    "draft": {"fps": 30, "crf": "23", "preset": "veryfast", "res": 720},
    "final": {"fps": 60, "crf": "16", "preset": "medium", "res": 1080},
    "max": {"fps": 60, "crf": "14", "preset": "slow", "res": 1440},
}

# x264 presets a config may ask for. Measured on a 7.5s 1080p60 line chart, the whole
# spread is ~3s of encode (veryfast 3.5s, medium 4.3s, slow 6.5s) and medium and slow come
# out the same size — chart content is flat enough that x264 runs out of wins early. So
# `final` sits on medium, and the override exists to trim seconds, not to change the file.
PRESETS = ("veryfast", "medium", "slow")

FONT_STACK = ["Inter", "Helvetica Neue", "Arial", "Liberation Sans", "DejaVu Sans"]
MONO_STACK = ["JetBrains Mono", "SF Mono", "Menlo", "Consolas", "DejaVu Sans Mono"]

# h264 has no alpha channel, so a transparent render changes codec and container both.
# ProRes 4444 is the intermediate every NLE ingests without a transcode, and prores_ks is
# a native FFmpeg encoder rather than an external library — so it is there in a minimal
# static build, where libvpx and friends often aren't.
ALPHA_CODEC = "prores_ks"


def output_extension(transparent=False):
    """Container a render lands in. app.py names the file, so it has to ask."""
    return ".mov" if transparent else ".mp4"


@dataclass
class Ctx:
    theme: dict
    w: int
    h: int
    fps: int
    crf: str
    preset: str
    dpi: int = 100
    transparent: bool = False

    @property
    def s(self) -> float:
        """Font/line scale so a 720p draft matches a 1080p final in proportion."""
        return min(self.w, self.h) / 1080.0

    @property
    def tall(self) -> bool:
        return self.h > self.w

    @property
    def bg(self) -> str:
        """Background fill, or no fill at all when exporting with an alpha channel.

        Anything that exists only to sit *behind* the chart reads this rather than the
        theme directly, so a transparent export drops the backdrop and leaves everything
        the viewer actually looks at untouched.
        """
        return "none" if self.transparent else self.theme["bg"]


def make_ctx(theme_name, aspect, quality, fps=None, res=None, transparent=False,
             preset=None) -> Ctx:
    theme = THEMES.get(theme_name, THEMES["midnight"])
    enc = ENCODE.get(quality, ENCODE["final"])
    sizes = SIZES.get(aspect, SIZES["16:9"])
    w, h = sizes.get(res or enc["res"], sizes[enc["res"]])
    # "auto" and anything unrecognised fall back to the tier's own preset, so the tier
    # stays in charge unless someone deliberately overrode it.
    if preset in (None, "auto") or preset not in PRESETS:
        preset = enc["preset"]
    return Ctx(theme=theme, w=w, h=h, fps=int(fps or enc["fps"]),
               crf=enc["crf"], preset=preset, transparent=bool(transparent))


# ---------------------------------------------------------------------------
# Motion helpers
# ---------------------------------------------------------------------------
def ease(name, t):
    t = np.clip(t, 0.0, 1.0)
    if name == "linear":
        return t
    if name == "inout":
        return np.where(t < 0.5, 4 * t**3, 1 - (-2 * t + 2) ** 3 / 2)
    if name == "expo":
        return np.where(t >= 1.0, 1.0, 1 - np.power(2, -10 * t))
    return 1.0 - (1.0 - t) ** 3  # "out"


def _plan(duration, hold, fps, easing, dense_n):
    """Map each frame to an index along a densely-sampled series."""
    n = max(int(duration * fps), 2)
    prog = ease(easing, np.linspace(0.0, 1.0, n))
    cut = np.clip((prog * (dense_n - 1)).astype(int), 1, dense_n - 1)
    return n, int(hold * fps), cut, prog


def _densify(x, y, n):
    xd = np.linspace(x[0], x[-1], n)
    return xd, np.interp(xd, x, y)


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------
# Left alone, every chart below draws into one fixed pair of axis limits: the whole range
# is in frame from the first frame to the last. That is the honest framing, and it is also
# why the opening second of a reveal is mostly empty and the closing second is too wide to
# read a number off. A camera move animates those limits instead.
#
# Two rules shape the implementation. The whole move is planned before the first frame is
# drawn, because `still=` has to answer for frame 200 without drawing the 199 before it —
# a camera that nudged its limits frame to frame, the way the race rows settle, would hand
# the still export a different frame than the video. And the head of the reveal is never
# allowed out of shot, whichever move is running: losing it reads as a bug, not a camera.
CAMERAS = {
    "locked": {"label": "Locked off",
               "desc": "The whole range in frame from the first frame to the last."},
    "pullback": {"label": "Pull back",
                 "desc": "Opens tight on the first days and widens as the line arrives."},
    "follow": {"label": "Follow",
               "desc": "A window travelling with the line, settling back to the whole "
                       "chart on the hold."},
    "push": {"label": "Push in",
             "desc": "Starts wide and dollies in, landing tight as the reveal ends."},
}

# How far each move travels, as the share of the full range its tightest frame shows. The
# moves get there differently, but they all answer the same question: how close in does it
# actually get?
TRAVEL = {
    "subtle": {"pullback": 0.45, "follow": 0.60, "push": 0.62},
    "standard": {"pullback": 0.28, "follow": 0.42, "push": 0.46},
    "bold": {"pullback": 0.15, "follow": 0.27, "push": 0.32},
}
TRAVELS = ("subtle", "standard", "bold")
CAMERA_Y = ("track", "hold")

_LEAD = 0.20         # share of a follow window kept ahead of the head
_PUSH_START = 0.25   # fraction of the reveal that passes before a push starts moving
_MIN_Y_VIEW = 0.18   # floor on a tracked y window, as a share of the resting one
_Y_PAD = 0.12        # headroom above and below the data in a tracked window
_SMOOTH_TAU = 0.25   # seconds a tracked y window takes to answer a change in the extremes


def head_track(pos, cut, hold_frames):
    """Where the reveal head sits at each frame, held still through the hold."""
    head = np.asarray(pos, float)[cut]
    if hold_frames:
        head = np.concatenate([head, np.full(hold_frames, head[-1])])
    return head


def _smooth(sig, fps):
    """Zero-phase smoothing over a planned signal — forward, then back over the result.

    A single forward pass would always arrive late, and late here means a new high sitting
    outside the frame for a few frames while the camera catches up. Running it in both
    directions cancels the lag, so the frame starts opening slightly *before* the bar that
    needs the room — which is what a camera operator who has read the script does.
    """
    out = np.asarray(sig, float).copy()
    if len(out) < 2:
        return out
    a = 1.0 - float(np.exp(-1.0 / (_SMOOTH_TAU * fps)))
    for i in range(1, len(out)):
        out[i] = out[i - 1] + (out[i] - out[i - 1]) * a
    for i in range(len(out) - 2, -1, -1):
        out[i] = out[i + 1] + (out[i] - out[i + 1]) * a
    return out


def _settle_ramp(hold_frames, fps):
    """0 to 1 over the front of the hold, so a move can land before the clip ends."""
    if hold_frames <= 0:
        return np.zeros(0)
    length = max(min(hold_frames * 0.65, 1.1 * fps), 1.0)
    return ease("inout", np.clip(np.arange(hold_frames) / length, 0.0, 1.0))


def _plan_x(move, tight, x_lo, x_hi, head, n_frames, hold_frames, fps):
    """One horizontal window per frame, in data units."""
    frames = n_frames + hold_frames
    span = x_hi - x_lo
    x0 = np.full(frames, float(x_lo))
    x1 = np.full(frames, float(x_hi))
    if move == "locked" or span <= 0:
        return x0, x1

    if move == "pullback":
        # The frame holds the drawn line with the head just inside the right edge, so the
        # opening is a close-up that widens as the data arrives. It reaches the full range
        # a little before the reveal ends and stays there: the last beat is the frame
        # people screenshot, and it has to show everything.
        win = np.clip((head - x_lo) / 0.85, tight * span, span)
        return x0, np.minimum(x0 + win, x_hi)

    if move == "follow":
        # Early on there is not enough drawn to fill the travelling window, so the frame
        # opens tight on what there is and grows into it. Starting at full width instead
        # would park the head at the left of a mostly empty frame for the first second,
        # which is the thing a moving camera is here to stop doing.
        win = np.clip((head - x_lo) / 0.85, tight * span * 0.35, tight * span)
        x1 = np.maximum(head + win * _LEAD, x_lo + win)
        x0 = x1 - win
        # Then hand the whole chart back. A replay that ends on a close-up never shows the
        # shape of the thing it just replayed. Mixed this way round rather than as
        # `a + (b - a) * k` so that k of exactly 1 lands on exactly the resting frame.
        if hold_frames:
            k = _settle_ramp(hold_frames, fps)
            x0[n_frames:] = x0[n_frames - 1] * (1 - k) + x_lo * k
            x1[n_frames:] = x1[n_frames - 1] * (1 - k) + x_hi * k
        return x0, x1

    # push: a slow dolly toward the closing action. It runs on the clock rather than on the
    # reveal's easing, so the move stays smooth whichever easing the reveal uses, and it
    # lands exactly as the reveal ends — a camera still creeping when the clip cuts looks
    # like a mistake rather than a decision. That clock is elapsed reveal, the same one the
    # head runs on: one frame in n rather than the one frame in n-1 `linspace` would give,
    # which costs four parts in a hundred thousand of the move at the moment it lands and
    # buys a dolly that is the same speed at 30fps and at 60.
    p = np.arange(n_frames) / float(max(n_frames, 1))
    z = ease("inout", np.clip((p - _PUSH_START) / (1.0 - _PUSH_START), 0.0, 1.0))
    if hold_frames:
        z = np.concatenate([z, np.full(hold_frames, z[-1])])
    x1 = np.full(frames, x_hi + span * 0.02)
    x0 = x1 - span * (1.0 - z * (1.0 - tight))
    return np.minimum(x0, head - span * 0.02), x1


def _plan_y(x, lo, hi, head, x0, x1, rest, mode, fps, weight=None, floor=None):
    """One vertical window per frame, framing what the horizontal window has drawn.

    Returns the two limits plus the raw visible peak, which is the only number the volume
    strip's single tick can sensibly sit on once the strip is being tracked.
    """
    frames = len(x0)
    peak = float(np.max(hi))
    if mode != "track":
        return (np.full(frames, float(rest[0])), np.full(frames, float(rest[1])),
                np.full(frames, peak))

    rest_span = float(rest[1] - rest[0])
    raw_lo = np.empty(frames)
    raw_hi = np.empty(frames)
    for i in range(frames):
        # Only what has been drawn is on screen, so the frame is built from that and not
        # from data the reveal has not reached — otherwise the camera opens up early to
        # make room for a spike the viewer cannot see yet, and the reveal loses its punch.
        a = int(np.searchsorted(x, x0[i], "left"))
        b = int(np.searchsorted(x, min(x1[i], head[i]), "right"))
        a = min(a, len(x) - 1)
        b = max(b, a + 1)
        raw_lo[i] = lo[a:b].min()
        raw_hi[i] = hi[a:b].max()

    # A quiet stretch magnified to fill the frame is just noise drawn large, which on a
    # price chart is a lie about how much happened. The floor on the window stops it.
    span = np.maximum(raw_hi - raw_lo, rest_span * _MIN_Y_VIEW)
    mid = (raw_hi + raw_lo) / 2.0
    top = _smooth(mid + span * (0.5 + _Y_PAD), fps)
    bot = _smooth(mid - span * (0.5 + _Y_PAD), fps)

    # A camera zooms; it doesn't stretch one axis and leave the other. `weight` is how far
    # the frame has actually moved in horizontally, and the vertical follows it by the
    # same amount — so a push that opens on the full width opens on the full height too,
    # instead of magnifying the first few days into a spike, and a pull back that has
    # reached the whole range is framed exactly as a locked camera would have framed it.
    if weight is not None:
        w = np.clip(weight, 0.0, 1.0)
        top = float(rest[1]) * (1 - w) + top * w
        bot = float(rest[0]) * (1 - w) + bot * w

    top = np.maximum(top, raw_hi + span * 0.02)  # smoothing may lag; never clip the data
    if floor is not None:
        return np.full(frames, float(floor)), top, raw_hi
    return np.minimum(bot, raw_lo - span * 0.02), top, raw_hi


class Camera:
    """Where the frame is pointed, planned once for the whole clip and read by index.

    `extent` and `rest_y` are the limits the chart would have used on its own. A locked
    camera hands exactly those back on every frame, which is what makes it free to leave
    the default alone — nothing about an existing config renders differently.
    """

    def __init__(self, cfg, ctx, *, x, lo, hi, head, n_frames, hold_frames,
                 extent=None, rest_y=None, log=False):
        self.move = cfg.get("camera", "locked")
        self.moving = self.move != "locked" and self.move in CAMERAS
        tight = TRAVEL.get(cfg.get("camera_travel", "standard"), TRAVEL["standard"])
        x_lo, x_hi = extent if extent else (float(x[0]), float(x[-1]))
        rest_y = rest_y if rest_y else (float(np.min(lo)), float(np.max(hi)))

        self._x = np.asarray(x, float)
        self._head = np.asarray(head, float)
        self._fps = ctx.fps
        self.frames = n_frames + hold_frames
        self.x0, self.x1 = _plan_x(self.move if self.moving else "locked",
                                   tight.get(self.move, 1.0), x_lo, x_hi, self._head,
                                   n_frames, hold_frames, ctx.fps)
        # How far in the frame has come, which is what the vertical follows.
        self._weight = 1.0 - (self.x1 - self.x0) / (x_hi - x_lo or 1.0)
        # A locked camera does not move, and that includes the vertical: the setting only
        # ever applies to a frame that is already travelling.
        mode = cfg.get("camera_y", "track") if self.moving else "hold"
        self.y0, self.y1, _ = _plan_y(self._x, lo, hi, self._head, self.x0, self.x1,
                                      rest_y, mode, ctx.fps, weight=self._weight)
        # A log axis cannot draw a bottom at or below zero, and the vertical is planned
        # with linear padding — on a low-priced series that padding crosses zero. Clamping
        # the planned floor keeps the move intact and only bites where it has to; the
        # padding rule itself stays linear, which is what a locked camera reproduces.
        if log:
            base = float(np.min(lo))
            floor = base * 0.9 if base > 0 else float(np.min(self.y1)) * 0.5
            self.y0 = np.maximum(self.y0, max(floor, 1e-9))
            self.y1 = np.maximum(self.y1, self.y0 * 1.001)
        self._mode_y = mode

    def track(self, lo, hi, rest, floor=None):
        """Plan a second axis against the same windows — the volume strip under candles."""
        return _plan_y(self._x, np.asarray(lo, float), np.asarray(hi, float), self._head,
                       self.x0, self.x1, rest, self._mode_y, self._fps,
                       weight=self._weight, floor=floor)

    def apply(self, ax, i):
        """Point the axes at frame `i` and return the index actually used."""
        i = min(max(int(i), 0), self.frames - 1)
        ax.set_xlim(self.x0[i], self.x1[i])
        ax.set_ylim(self.y0[i], self.y1[i])
        return i

    def width(self, i):
        return float(self.x1[i] - self.x0[i])

    def height(self, i):
        return float(self.y1[i] - self.y0[i])

    def bottom(self, i):
        return float(self.y0[i])

    def right(self, i):
        return float(self.x1[i])


def _date_ticks(ax, span_days):
    """Match the date format to how much time is actually in frame.

    Only a moving camera needs this. A locked frame shows the range the user picked, and
    they picked the format along with it; a camera changes the visible span underneath
    them, and "Jan 2024" three ticks running is the result if the format does not follow.
    """
    ax.xaxis.set_major_formatter(
        mdates.DateFormatter("%d %b" if span_days <= 120 else "%b %Y"))


# ---------------------------------------------------------------------------
# Figure scaffolding
# ---------------------------------------------------------------------------
def _new_fig(ctx):
    plt.rcParams["font.family"] = FONT_STACK
    fig = plt.figure(figsize=(ctx.w / ctx.dpi, ctx.h / ctx.dpi), dpi=ctx.dpi)
    fig.patch.set_facecolor(ctx.bg)
    return fig


def _footer_text(footer):
    """Combine the user's footer with a data-source note.

    Yahoo says nothing — it's the assumed source. Stooq and demo data announce themselves,
    because a total return drawn from either can differ from what the viewer expects and
    these charts get narrated on camera.
    """
    note = datasrc.attribution()
    if not note:
        return footer
    return f"{footer}  ·  {note}" if footer else note


def _titles(fig, ctx, title, subtitle, footer):
    t = ctx.theme
    top = 0.955 if not ctx.tall else 0.965
    if title:
        fig.text(0.07, top, title, color=t["text"], fontsize=34 * ctx.s,
                 fontweight="bold", va="top")
    if subtitle:
        fig.text(0.07, top - (0.052 if not ctx.tall else 0.030), subtitle,
                 color=t["muted"], fontsize=17 * ctx.s, va="top",
                 fontfamily=MONO_STACK)
    footer = _footer_text(footer)
    if footer:
        fig.text(0.93, 0.035, footer, color=t["muted"], fontsize=13 * ctx.s,
                 ha="right", va="center", alpha=0.8)


def _plot_area(ctx, has_title):
    """Axes rect tuned per aspect ratio, leaving room for right-edge labels."""
    if ctx.tall:
        return [0.15, 0.13, 0.71, 0.76 if has_title else 0.80]
    return [0.07, 0.11, 0.79, 0.70 if has_title else 0.78]


def _span_days(index):
    """How much time is on screen, or None when there isn't enough to tell."""
    if index is None or len(index) < 2:
        return None
    return (index[-1] - index[0]).total_seconds() / 86400.0


def _axis_fmt(index=None):
    """Tick label format that suits the window.

    Date labels have to follow the span or they say nothing: a week of bars under "%b %Y"
    repeats one month across the axis, a single session repeats it at every tick, and a
    decade under "%d %b" is unreadable clutter. Defaults to the month-and-year form the
    charts used before there was anything but multi-year windows.
    """
    days = _span_days(index)
    if days is None:
        return "%b %Y"
    if days <= 2:
        return "%H:%M"
    if days <= 90:
        return "%d %b"
    if days <= 1500:
        return "%b %Y"
    return "%Y"


def _style_axes(ax, ctx, y_fmt=None, x_dates=True, index=None, log=False):
    t = ctx.theme
    ax.set_facecolor(ctx.bg)
    ax.grid(True, color=t["grid"], linewidth=0.8 * ctx.s, alpha=0.9)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(t["axis"])
    ax.tick_params(colors=t["muted"], labelsize=13 * ctx.s, length=0)
    if log:
        # Set the scale before touching the tick labels — changing it afterwards would
        # rebuild them and lose the font.
        ax.set_yscale("log")
        ax.yaxis.set_major_locator(_PriceLogLocator())
        ax.yaxis.set_minor_locator(mticker.NullLocator())
    for lbl in ax.get_xticklabels() + ax.get_yticklabels():
        lbl.set_fontfamily(MONO_STACK)
    if x_dates:
        ax.xaxis.set_major_locator(
            mdates.AutoDateLocator(minticks=3, maxticks=5 if ctx.tall else 8))
        ax.xaxis.set_major_formatter(mdates.DateFormatter(_axis_fmt(index)))
    if y_fmt:
        ax.yaxis.set_major_formatter(y_fmt)


# ---------------------------------------------------------------------------
# X axis: time for daily, bar position for intraday
# ---------------------------------------------------------------------------
# Daily bars belong on a real date axis — the gaps are weekends, and reading the spacing as
# elapsed time is correct. Intraday cannot have that. Five-minute bars across a week are
# two thirds overnight by wall clock, so a time axis spends most of the frame drawing flat
# nothing and the animated head crawls across dead space between sessions. Intraday is
# plotted by bar position and the ticks carry the timestamps instead, which is what the
# candle chart has always done. Daily keeps the old path exactly, so its output is unchanged.
def _interval(cfg):
    return cfg.get("interval") or datasrc.DEFAULT_INTERVAL


def _intraday(cfg):
    return datasrc.is_intraday(_interval(cfg))


def _one_session(index):
    return len(index) > 0 and index[0].normalize() == index[-1].normalize()


def _x_values(index, intraday):
    if intraday:
        return np.arange(len(index), dtype=float)
    return mdates.date2num(index.to_pydatetime())


def _at_position(index, pos):
    """Timestamp for a fractional bar position, for readouts on a positional axis."""
    return index[int(np.clip(round(pos), 0, len(index) - 1))]


def _position_ticks(ax, ctx, index, view=None):
    """Label a positional axis with the timestamps those positions stand for.

    `view` is the visible span in bar positions, and is what `_date_ticks` is to a date
    axis: a locked frame shows the whole index and can tick all of it, but a moving camera
    shows a slice, and ticking the full index would leave most of the labels off screen.
    """
    n = len(index)
    lo, hi = (0, n - 1) if view is None else view
    lo, hi = max(int(np.floor(lo)), 0), min(int(np.ceil(hi)), n - 1)
    if hi - lo < 1:  # a window this tight has nothing to label; fall back to the range
        lo, hi = 0, n - 1
    if _one_session(index[lo:hi + 1]):
        step = max((hi - lo + 1) // (5 if ctx.tall else 8), 1)
        pos = np.arange(lo, hi + 1, step)
        labels = [f"{index[i]:%H:%M}" for i in pos]
    else:
        # One tick per session: it never repeats a day label and lands the gridlines on
        # the opens, which is where the eye looks for them anyway.
        days = index.normalize()
        opens = np.flatnonzero(np.r_[True, days[1:] != days[:-1]])
        opens = opens[(opens >= lo) & (opens <= hi)]
        pos = (opens[::max(len(opens) // (4 if ctx.tall else 7), 1)] if len(opens)
               else np.array([lo]))
        labels = [f"{index[i]:%d %b}" for i in pos]
    ax.set_xticks(pos)
    ax.set_xticklabels(labels)
    for lbl in ax.get_xticklabels():
        lbl.set_fontfamily(MONO_STACK)


def _range_label(index, intraday):
    """The span shown under the title, at the precision the window deserves.

    A fixed month-and-year turns any window shorter than a couple of months into
    "Jun 2024 – Jun 2024", and the date presets make those the common case — so a short
    window names the day and a single session names the hours.
    """
    a, b = index[0], index[-1]
    if intraday:
        if _one_session(index):
            return f"{a:%d %b %Y}, {a:%H:%M} – {b:%H:%M}"
        return f"{a:%d %b} – {b:%d %b %Y}"
    if (_span_days(index) or 0) <= 120:
        left = f"{a:%d %b}" if a.year == b.year else f"{a:%d %b %Y}"
        return f"{left} – {b:%d %b %Y}"
    return f"{a:%b %Y} – {b:%b %Y}"


def _stamp(ts, index, intraday, daily="%d %b %Y"):
    """Label for one moment — the candle readout and the race clock."""
    if not intraday:
        return format(ts, daily)
    return f"{ts:%H:%M}" if _one_session(index) else f"{ts:%d %b %H:%M}"


def _money(v, _=None):
    a = abs(v)
    if a >= 100:
        return f"${v:,.0f}"
    return f"${v:,.2f}" if a < 10 else f"${v:,.1f}"


def _decimals(lo, hi, floor=0):
    """Decimals needed to keep gridline labels distinct across this range.

    _money picks precision from a value's magnitude, which is right for a single readout
    and wrong for an axis: a $159–$162 range rounds to whole dollars and prints the same
    label three times down the side. Daily ranges are wide enough that the magnitude rule
    already holds, so `floor` keeps their labels exactly as they were.
    """
    step = abs(hi - lo) / 7
    need = 0 if step >= 1 else 1 if step >= 0.1 else 2
    return max(need, floor)


def _money_axis(lo, hi):
    mag = max(abs(lo), abs(hi))
    floor = 0 if mag >= 100 else 1 if mag >= 10 else 2
    dec = _decimals(lo, hi, floor)
    return lambda v, _=None: f"${v:,.{dec}f}"


def _num_axis(lo, hi):
    dec = _decimals(lo, hi)
    return lambda v, _=None: f"{v:,.{dec}f}"


def _glow(ax, color, ctx, zorder=2):
    return [ax.plot([], [], color=color, lw=w * ctx.s, alpha=a,
                    solid_capstyle="round", zorder=zorder)[0]
            for w, a in ((11, 0.05), (7, 0.09), (4.5, 0.15))]


# ---------------------------------------------------------------------------
# Y axis: linear or log
# ---------------------------------------------------------------------------
class _PriceLogLocator(mticker.Locator):
    """Readable ticks for a log price axis.

    Matplotlib's log locator ladders within each decade, which suits a price chart badly
    once the range doesn't line up with one. Over 56 to 192 it puts ticks at 60/70/80/90
    and then 100 — and nothing at all between 100 and 192, because the next rungs are 200
    and up. Half the axis ends up unlabelled.

    So pick from the range actually in view. Inside one decade, round steps cover the whole
    axis evenly and log and linear are visually close anyway. Beyond it, the decade ladder
    is the readable choice, thinned as the span grows.

    This is a locator rather than a decision made up front because the limits aren't known
    until after the axes are styled.
    """

    def __init__(self):
        self._linear = mticker.MaxNLocator(nbins=7, steps=[1, 2, 2.5, 5, 10])
        self._log = mticker.LogLocator(base=10.0)

    def set_axis(self, axis):
        super().set_axis(axis)
        self._linear.set_axis(axis)
        self._log.set_axis(axis)

    def tick_values(self, vmin, vmax):
        if vmin <= 0 or vmax <= vmin:
            return self._log.tick_values(vmin, vmax)
        decades = np.log10(vmax / vmin)
        if decades < 1.0:
            return self._linear.tick_values(vmin, vmax)
        self._log.set_params(subs=(1.0,) if decades > 2.5 else (1.0, 2.0, 5.0))
        return self._log.tick_values(vmin, vmax)

    def __call__(self):
        return self.tick_values(*self.axis.get_view_interval())


def _log_ok(cfg, lo):
    """Whether to draw a log y-axis.

    Only when the data allows it — a range reaching zero or below can't be drawn on a log
    axis, and quietly staying linear beats failing the render over an axis preference.
    """
    return bool(cfg.get("log_scale")) and lo > 0


def _scale_note(log):
    """A log axis flattens a big move, and a viewer watching the clip has no other way to
    tell. The default subtitle says so; a subtitle the user typed is left alone."""
    return "   ·   log scale" if log else ""






# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------
# How far the averages trail the reveal head, in seconds. An average is a lagging
# indicator, and letting it arrive a beat late says so in the motion rather than in a
# caption — the price moves, the average answers. Off by default, because every render
# made before this existed had the two arriving together.
MA_LAG = {"none": 0.0, "subtle": 0.15, "standard": 0.30, "bold": 0.55}
MA_LAGS = tuple(MA_LAG)

_MA_CATCH = 0.25    # share of the reveal spent closing the gap again
_MA_MAX_LAG = 0.18  # ceiling on the delay, as a share of the reveal


def ma_track(cfg, cut, n_frames, fps):
    """Frame -> series index for the averages, which may trail the price line.

    Planned off `cut` rather than accumulated, for the reason the camera is: `still=`
    asks for frame 200 without drawing the 199 before it, so a lag that held a little
    back each frame would hand the still export a different frame than the video.

    The delay is set in seconds — the same beat at 30fps and at 60 — and it closes over
    the end of the reveal so the averages land with the price line. A clip ending on a
    short average reads as a chart that failed to finish drawing rather than as a
    decision, and the last frame is the one people screenshot.
    """
    lag = MA_LAG.get(cfg.get("ma_lag"), 0.0)
    if lag <= 0 or n_frames < 2:
        return cut
    # Capped against the reveal as well as set in seconds: on a two-second clip a third of
    # a second behind is a third of the chart, which stops reading as a trailing average
    # and starts reading as a second, shorter one.
    delay = min(lag * fps, n_frames * _MA_MAX_LAG)
    i = np.arange(n_frames, dtype=float)
    close = ease("inout", np.clip((1.0 - i / (n_frames - 1)) / _MA_CATCH, 0.0, 1.0))
    return cut[np.clip(i - delay * close, 0, n_frames - 1).astype(int)]


def _fetch_with_ma(tk, cfg, periods):
    """Fetch a ticker plus any moving averages, warmed up before the chart starts.

    An average has no value until it has its whole window of closes. Fetched naively a
    200-day line would only begin 200 bars into the chart — two thirds of the way across a
    one-year range, which looks broken next to any charting platform. Pulling extra history
    and trimming it afterwards costs one more cache entry and nothing else.

    Returns (visible_df, {period: Series over the full fetched index}); the caller lines
    the averages up with whatever bars it ends up drawing.
    """
    win = datasrc.window(cfg)
    if not periods:
        return datasrc.fetch(tk, **win), {}

    interval = win.get("interval") or datasrc.DEFAULT_INTERVAL
    sessions = win.get("sessions")
    # Bars to sessions to calendar days, with slack for weekends and holidays. Reading the
    # run-up off the interval is what keeps it sane on intraday: 200 five-minute bars is
    # about three sessions of lead, not the best part of a year.
    per_session = max(datasrc.periods_per_year(interval) / 252.0, 1.0)
    lead_days = int(max(periods) / per_session * 1.5) + 14
    limit = datasrc.max_lookback_days(interval)
    if limit:  # asking past what the source keeps returns silence, not an error
        lead_days = min(lead_days, limit - 1)
    lead = pd.Timestamp(win["start"]) - pd.Timedelta(days=lead_days)

    # Fetched without `sessions`, because the trim is what makes the chart one session and
    # the average needs the sessions before it to have a value at the left edge. Trimming
    # here rather than fetching twice keeps this to the one request it always was.
    full = datasrc.fetch(tk, lead.strftime("%Y-%m-%d"), win.get("end"), interval)
    visible = full.loc[full.index >= pd.Timestamp(win["start"])]
    if sessions and not visible.empty:
        days = visible.index.normalize().unique()
        visible = visible.loc[visible.index.normalize() >= days[-int(sessions):][0]]
    if len(visible) < 2:  # everything available predates the requested start
        visible = full
    return visible, {p: full["Close"].rolling(p).mean() for p in periods}


def _align_ma(mas, index, ffill=False):
    """Line the warmed averages up with the bars actually drawn.

    Candles resample to weekly or monthly for long ranges, and those period-end labels
    aren't trading days — they need the last daily value at or before each bar.
    """
    method = "ffill" if ffill else None
    return {p: s.reindex(index, method=method).to_numpy(float) for p, s in mas.items()}


def _dense_ma(x, ma, xd):
    """Upsample an average onto the dense timeline, keeping its leading gap.

    The bars before the window fills have no value, and np.interp would smear those NaNs
    across the neighbouring interval, so interpolate only from the first real value on.
    """
    ok = ~np.isnan(ma)
    if not ok.any():
        return None
    out = np.full(len(xd), np.nan)
    live = xd >= x[int(ok.argmax())]
    out[live] = np.interp(xd[live], x[ok], ma[ok])
    return out


def _extend_range(lo, hi, arrays):
    """Widen a y-range to cover the averages too.

    One warmed from before the chart starts can easily sit outside the visible price
    range — a stock that fell hard just before the window has its 200-day well above it.
    """
    for arr in arrays:
        if arr is not None and np.isfinite(arr).any():
            lo = min(lo, float(np.nanmin(arr)))
            hi = max(hi, float(np.nanmax(arr)))
    return lo, hi


def _ma_lines(ax, ctx, series, zorder=3, avoid=()):
    """One subordinate line per average, coloured from the theme's series palette.

    `series` is a list of (period, values); returns (artist, values) pairs ready to
    animate, dropping any average with no data in range.

    `avoid` is any colour already carrying meaning on this chart — the price line, the
    candle bodies. Every theme's series palette overlaps its own up/down colours, so
    without this the 200-day comes out the same green as the thing it's drawn against.
    """
    avoid = {avoid} if isinstance(avoid, str) else set(avoid)
    palette = [c for c in ctx.theme["series"] if c not in avoid] or ctx.theme["series"]
    out = []
    for i, (period, vals) in enumerate(series):
        if vals is None or not np.isfinite(vals).any():
            continue
        (ln,) = ax.plot([], [], color=palette[i % len(palette)], lw=1.5 * ctx.s,
                        alpha=0.85, solid_capstyle="round", zorder=zorder,
                        label=f"{period}-day MA")
        out.append((ln, vals))
    return out


def _ma_key(ax, ctx, pairs, rising):
    """Frameless key for the averages, parked in whichever corner the price line leaves
    empty — a rising line clears the top left, a falling one clears the bottom left."""
    if not pairs:
        return
    leg = ax.legend(handles=[ln for ln, _ in pairs],
                    loc="upper left" if rising else "lower left",
                    frameon=False, fontsize=13 * ctx.s, handlelength=1.7,
                    labelspacing=0.35, borderaxespad=1.0)
    for txt in leg.get_texts():
        txt.set_color(ctx.theme["muted"])
        txt.set_fontfamily(MONO_STACK)
    leg.set_zorder(6)


_FFMPEG_CHECKED = False


def _resolve_ffmpeg():
    """Point matplotlib at the pip-installed ffmpeg when the system has none.

    A system ffmpeg wins wherever one exists — it is leaner than carrying a second copy,
    and the Docker image apt-gets it for that reason. requirements.txt ships
    imageio-ffmpeg so a fresh clone encodes without a separate install, and then on a
    machine that already has ffmpeg on PATH it is never imported.
    """
    global _FFMPEG_CHECKED
    if _FFMPEG_CHECKED:
        return
    _FFMPEG_CHECKED = True
    if shutil.which("ffmpeg"):
        return
    try:
        import imageio_ffmpeg
    except ImportError:
        return  # let FFMpegWriter raise its own, clearer, error
    matplotlib.rcParams["animation.ffmpeg_path"] = imageio_ffmpeg.get_ffmpeg_exe()


def _export(fig, anim, path, ctx, progress):
    _resolve_ffmpeg()
    # Containers advertise the host's CPU count, so auto threading can spawn dozens of
    # threads whose per-thread buffers blow a small memory limit and get ffmpeg
    # OOM-killed. Four threads keeps memory bounded and encoding is not the bottleneck
    # here anyway — matplotlib drawing the frames is.
    threads = ["-threads", "4"]
    if ctx.transparent:
        # qscale 9 is visually lossless on frames this flat. ProRes 4444 files are large
        # by design — it's an edit-ready intermediate, not a delivery format.
        codec = ALPHA_CODEC
        args = ["-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-qscale:v", "9",
                *threads]
    else:
        codec = "libx264"
        args = ["-pix_fmt", "yuv420p", "-crf", ctx.crf, "-preset", ctx.preset,
                *threads, "-movflags", "+faststart"]

    writer = FFMpegWriter(fps=ctx.fps, codec=codec, bitrate=-1, extra_args=args)
    anim.save(path, writer=writer, dpi=ctx.dpi,
              savefig_kwargs={"facecolor": ctx.bg},
              progress_callback=progress)
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# 1. Single-ticker line
# ---------------------------------------------------------------------------
def render_line(cfg, ctx, out, progress=None, still=None):
    tk = cfg["tickers"][0]
    intraday = _intraday(cfg)
    periods = cfg.get("ma") or []
    df, ma_series = _fetch_with_ma(tk, cfg, periods)
    x = _x_values(df.index, intraday)
    y = df["Close"].to_numpy(float)

    dense_n = max(int(cfg["duration"] * ctx.fps) * 2, 2000)
    xd, yd = _densify(x, y, dense_n)
    n_frames, hold, cut, _ = _plan(cfg["duration"], cfg["hold"], ctx.fps,
                                   cfg["easing"], dense_n)

    mas = _align_ma(ma_series, df.index)
    ma_vals = [(p, _dense_ma(x, mas[p], xd)) for p in periods]
    ma_cut = ma_track(cfg, cut, n_frames, ctx.fps)
    lo, hi = _extend_range(y.min(), y.max(), [v for _, v in ma_vals])
    log = _log_ok(cfg, lo)

    t = ctx.theme
    up = y[-1] >= y[0]
    color = t["up"] if up else t["down"]
    pct = (y[-1] / y[0] - 1) * 100

    fig = _new_fig(ctx)
    _titles(fig, ctx, cfg.get("title") or tk,
            cfg.get("subtitle")
            or f"{_range_label(df.index, intraday)}   ·   {pct:+.1f}%{_scale_note(log)}",
            cfg.get("footer"))
    ax = fig.add_axes(_plot_area(ctx, True))
    _style_axes(ax, ctx, y_fmt=_money_axis(y.min(), y.max()), x_dates=not intraday,
                log=log)

    pad = (y.max() - y.min()) * 0.12 or y.max() * 0.05
    cam = Camera(cfg, ctx, x=xd, lo=yd, hi=yd, head=head_track(xd, cut, hold),
                 n_frames=n_frames, hold_frames=hold,
                 rest_y=(y.min() - pad, y.max() + pad), log=log)
    cam.apply(ax, 0)
    if intraday:
        _position_ticks(ax, ctx, df.index, ax.get_xlim())

    ma_pairs = _ma_lines(ax, ctx, ma_vals, avoid=color)
    _ma_key(ax, ctx, ma_pairs, rising=up)
    glow = _glow(ax, color, ctx)
    (line,) = ax.plot([], [], color=color, lw=2.6 * ctx.s, solid_capstyle="round",
                      solid_joinstyle="round", zorder=4)
    (head,) = ax.plot([], [], "o", color=color, markersize=9 * ctx.s,
                      markeredgecolor=ctx.bg, markeredgewidth=2 * ctx.s, zorder=5)
    readout = ax.text(0, 0, "", color=t["text"], fontsize=22 * ctx.s,
                      fontweight="bold", ha="left", va="center", zorder=6,
                      fontfamily=MONO_STACK)
    fill = [None]

    def draw(i):
        i = cam.apply(ax, i)
        k = cut[min(i, n_frames - 1)]
        xs, ys = xd[:k], yd[:k]
        line.set_data(xs, ys)
        for g in glow:
            g.set_data(xs, ys)
        km = ma_cut[min(i, n_frames - 1)]
        for ln, vals in ma_pairs:
            ln.set_data(xd[:km], vals[:km])
        head.set_data([xs[-1]], [ys[-1]])
        if fill[0] is not None:
            fill[0].remove()
        # The fill is anchored to the bottom of the frame, not to a fixed price, so a
        # moving camera slides the chart without dragging a floating slab of colour.
        fill[0] = ax.fill_between(xs, cam.bottom(i), ys, color=color, alpha=0.10,
                                  linewidth=0, zorder=1)
        readout.set_position((xs[-1] + cam.width(i) * 0.012, ys[-1]))
        readout.set_text(_money(ys[-1]))
        if cam.moving:
            # A date axis re-formats; a positional one re-labels. Same job either way —
            # the camera changed the visible span underneath the ticks.
            if intraday:
                _position_ticks(ax, ctx, df.index, ax.get_xlim())
            else:
                _date_ticks(ax, cam.width(i))
        return ()

    if still is not None:
        draw(int(n_frames * still))
        return fig
    anim = FuncAnimation(fig, draw, frames=n_frames + hold, interval=1000 / ctx.fps)
    return _export(fig, anim, out, ctx, progress)


# ---------------------------------------------------------------------------
# 2. Multi-ticker comparison
# ---------------------------------------------------------------------------
def render_compare(cfg, ctx, out, progress=None, still=None):
    intraday = _intraday(cfg)
    frames = datasrc.fetch_many(cfg["tickers"], **datasrc.window(cfg))
    closes = pd.DataFrame({k: v["Close"] for k, v in frames.items()}).dropna()
    if closes.empty:
        raise ValueError("Tickers have no overlapping trading days.")

    normalize = cfg.get("normalize", True)
    vals = closes / closes.iloc[0] * 100 if normalize else closes
    x = _x_values(closes.index, intraday)

    dense_n = max(int(cfg["duration"] * ctx.fps) * 2, 2000)
    xd = np.linspace(x[0], x[-1], dense_n)
    series = {c: np.interp(xd, x, vals[c].to_numpy(float)) for c in vals.columns}
    n_frames, hold, cut, _ = _plan(cfg["duration"], cfg["hold"], ctx.fps,
                                   cfg["easing"], dense_n)

    allv = np.concatenate(list(series.values()))
    log = _log_ok(cfg, float(allv.min()))

    t = ctx.theme
    palette = t["series"]
    fig = _new_fig(ctx)
    sub = ("Indexed to 100" if normalize else "Closing price") + \
          f"   ·   {_range_label(closes.index, intraday)}{_scale_note(log)}"
    _titles(fig, ctx, cfg.get("title") or " vs ".join(vals.columns),
            cfg.get("subtitle") or sub, cfg.get("footer"))
    ax = fig.add_axes(_plot_area(ctx, True))

    allv = np.concatenate(list(series.values()))
    _style_axes(ax, ctx, x_dates=not intraday, log=log,
                y_fmt=(_num_axis if normalize else _money_axis)(allv.min(), allv.max()))

    stack = np.vstack([series[c] for c in vals.columns])
    pad = (stack.max() - stack.min()) * 0.12
    cam = Camera(cfg, ctx, x=xd, lo=stack.min(axis=0), hi=stack.max(axis=0),
                 head=head_track(xd, cut, hold), n_frames=n_frames, hold_frames=hold,
                 rest_y=(stack.min() - pad, stack.max() + pad), log=log)
    cam.apply(ax, 0)
    if intraday:
        _position_ticks(ax, ctx, closes.index, ax.get_xlim())
    if normalize:
        ax.axhline(100, color=t["axis"], lw=1.2 * ctx.s, ls=(0, (4, 4)), zorder=1)

    lines, labels = {}, {}
    for i, c in enumerate(vals.columns):
        col = palette[i % len(palette)]
        (ln,) = ax.plot([], [], color=col, lw=2.6 * ctx.s, solid_capstyle="round",
                        zorder=4)
        lines[c] = ln
        labels[c] = ax.text(0, 0, "", color=col, fontsize=17 * ctx.s,
                            fontweight="bold", ha="left", va="center", zorder=6,
                            fontfamily=MONO_STACK)

    def draw(i):
        i = cam.apply(ax, i)
        k = cut[min(i, n_frames - 1)]
        # Nudge labels apart so overlapping series stay readable. The gap is measured
        # against the frame rather than the data, so it stays a constant distance on
        # screen while a tracking camera changes what a price is worth in pixels.
        ends = sorted(((series[c][k - 1], c) for c in series), reverse=True)
        min_gap = cam.height(i) * 0.045
        placed = []
        for v, c in ends:
            pos = v if not placed else min(v, placed[-1] - min_gap)
            placed.append(pos)
            lines[c].set_data(xd[:k], series[c][:k])
            labels[c].set_position((cam.right(i) + cam.width(i) * 0.015, pos))
            labels[c].set_text(f"{c} {v:,.0f}" if normalize else f"{c} {_money(v)}")
        if cam.moving:
            # A date axis re-formats; a positional one re-labels. Same job either way —
            # the camera changed the visible span underneath the ticks.
            if intraday:
                _position_ticks(ax, ctx, closes.index, ax.get_xlim())
            else:
                _date_ticks(ax, cam.width(i))
        return ()

    if still is not None:
        draw(int(n_frames * still))
        return fig
    anim = FuncAnimation(fig, draw, frames=n_frames + hold, interval=1000 / ctx.fps)
    return _export(fig, anim, out, ctx, progress)


# ---------------------------------------------------------------------------
# 3. Candlestick reveal
# ---------------------------------------------------------------------------
def render_candles(cfg, ctx, out, progress=None, still=None):
    tk = cfg["tickers"][0]
    intraday = _intraday(cfg)
    periods = cfg.get("ma") or []
    df, ma_series = _fetch_with_ma(tk, cfg, periods)
    max_c = int(cfg.get("max_candles", 90))
    if len(df) > max_c:
        if intraday:
            # Rolling daily bars up into weeks is the right answer for a multi-year chart
            # and the wrong one here — it would throw away the intraday shape that is the
            # entire reason for asking. Keep the most recent candles at the chosen interval.
            df = df.iloc[-max_c:]
        else:
            rule = "W" if len(df) / 5 <= max_c else "ME"
            df = df.resample(rule).agg({"Open": "first", "High": "max", "Low": "min",
                                        "Close": "last", "Volume": "sum"}).dropna()
            if len(df) > max_c:
                df = df.iloc[-max_c:]

    n = len(df)
    o, h, l, c = (df[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    vol = df["Volume"].to_numpy(float)
    idx = np.arange(n)

    n_frames, hold, cut, _ = _plan(cfg["duration"], cfg["hold"], ctx.fps,
                                   cfg["easing"], n + 1)

    # The averages were computed on daily closes above, before any rollup — so a "50-day"
    # line still means fifty days on a chart whose candles are weeks.
    mas = _align_ma(ma_series, df.index, ffill=True)
    ma_vals = [(p, mas[p]) for p in periods]
    ma_cut = ma_track(cfg, cut, n_frames, ctx.fps)
    lo, hi = _extend_range(float(l.min()), float(h.max()), [v for _, v in ma_vals])
    log = _log_ok(cfg, lo)

    t = ctx.theme
    fig = _new_fig(ctx)
    pct = (c[-1] / o[0] - 1) * 100
    _titles(fig, ctx, cfg.get("title") or tk,
            cfg.get("subtitle")
            or f"{_range_label(df.index, intraday)}   ·   {pct:+.1f}%{_scale_note(log)}",
            cfg.get("footer"))

    rect = _plot_area(ctx, True)
    vol_h = rect[3] * 0.20
    ax = fig.add_axes([rect[0], rect[1] + vol_h + rect[3] * 0.06,
                       rect[2], rect[3] - vol_h - rect[3] * 0.06])
    axv = fig.add_axes([rect[0], rect[1], rect[2], vol_h], sharex=ax)
    _style_axes(ax, ctx, y_fmt=_money_axis(l.min(), h.max()), x_dates=False, log=log)
    # The volume strip stays linear whatever the price axis does — it's a magnitude
    # comparison between adjacent bars, not a growth curve.
    _style_axes(axv, ctx, y_fmt=lambda v, _: f"{v/1e6:,.0f}M", x_dates=False)
    ax.tick_params(labelbottom=False)
    axv.grid(False)

    pad = (h.max() - l.min()) * 0.10
    # The camera works in candle-index space here rather than in dates, which is the same
    # space the axes are already in. `extent` is the resting (-1, n) so a locked camera
    # frames the row exactly as it always did, half a candle clear at either end.
    cam = Camera(cfg, ctx, x=idx.astype(float), lo=l, hi=h,
                 head=head_track(idx, np.clip(cut - 1, 0, n - 1), hold),
                 n_frames=n_frames, hold_frames=hold, extent=(-1, n),
                 rest_y=(l.min() - pad, h.max() + pad), log=log)
    # Volume rides the same windows. Left on the full range it would flatten to nothing
    # the moment the camera moved in on a quiet stretch.
    vol_bot, vol_top, vol_peak = cam.track(np.zeros(n), vol,
                                           rest=(0.0, vol.max() * 1.15), floor=0.0)
    cam.apply(ax, 0)

    if intraday:
        _position_ticks(axv, ctx, df.index)
    else:
        step = max(n // 6, 1)
        axv.set_xticks(idx[::step])
        axv.set_xticklabels([f"{d:%b %y}" for d in df.index[::step]])
        for lbl in axv.get_xticklabels():
            lbl.set_fontfamily(MONO_STACK)

    def retick(i):
        """Re-label the date axis for whatever candles are actually in frame."""
        vis = idx[(idx >= cam.x0[i]) & (idx <= cam.x1[i])]
        if not len(vis):
            return
        picks = vis[::max(len(vis) // 6, 1)]
        days = (df.index[picks[-1]] - df.index[picks[0]]).days
        # Inside a single session there is no day to name — every tick would read the same
        # date — so the clock is the only thing that distinguishes them.
        if intraday and _one_session(df.index[picks]):
            fmt = "%H:%M"
        else:
            fmt = "%d %b" if days <= 120 else "%b %y"
        axv.set_xticks(picks)
        axv.set_xticklabels([f"{df.index[j]:{fmt}}" for j in picks])
        for lbl in axv.get_xticklabels():
            lbl.set_fontfamily(MONO_STACK)

    wicks = LineCollection([], linewidths=1.4 * ctx.s, zorder=3)
    bodies = PolyCollection([], zorder=4, linewidths=0)
    vbars = PolyCollection([], zorder=3, linewidths=0)
    ax.add_collection(wicks)
    ax.add_collection(bodies)
    axv.add_collection(vbars)
    # The plate behind the readout keeps theme["bg"] rather than ctx.bg on purpose: it is
    # there to make the number legible over the candles, and it has the same job over
    # whatever footage a transparent export gets composited onto.
    readout = ax.text(0.985, 0.94, "", transform=ax.transAxes, color=t["text"],
                      fontsize=20 * ctx.s, fontweight="bold", ha="right",
                      va="top", fontfamily=MONO_STACK, zorder=6,
                      bbox=dict(boxstyle="round,pad=0.4", facecolor=t["bg"],
                                edgecolor="none", alpha=0.85))

    # Above the candle bodies, not below — an average is meant to be read against them.
    ma_pairs = _ma_lines(ax, ctx, ma_vals, zorder=5, avoid=(t["up"], t["down"]))
    _ma_key(ax, ctx, ma_pairs, rising=c[-1] >= o[0])

    bull, bear = t["up"], t["down"]
    colors = [bull if c[i] >= o[i] else bear for i in range(n)]
    w = 0.34

    def draw(i):
        i = cam.apply(ax, i)
        axv.set_ylim(vol_bot[i], vol_top[i])
        axv.set_yticks([vol_peak[i]])
        if cam.moving:
            retick(i)
        k = cut[min(i, n_frames - 1)]
        km = ma_cut[min(i, n_frames - 1)]
        for ln, vals in ma_pairs:
            ln.set_data(idx[:km], vals[:km])
        wick_seg, body_v, vol_v, cols, vcols = [], [], [], [], []
        for j in range(k):
            wick_seg.append([(j, l[j]), (j, h[j])])
            top, bot = max(o[j], c[j]), min(o[j], c[j])
            if top - bot < (h.max() - l.min()) * 0.002:
                top = bot + (h.max() - l.min()) * 0.002
            body_v.append([(j - w, bot), (j + w, bot), (j + w, top), (j - w, top)])
            vol_v.append([(j - w, 0), (j + w, 0), (j + w, vol[j]), (j - w, vol[j])])
            cols.append(colors[j])
            vcols.append(colors[j])
        wicks.set_segments(wick_seg)
        wicks.set_color(cols)
        bodies.set_verts(body_v)
        bodies.set_color(cols)
        vbars.set_verts(vol_v)
        vbars.set_color(vcols)
        vbars.set_alpha(0.45)
        readout.set_text(f"{_money(c[k-1])}   {_stamp(df.index[k-1], df.index, intraday)}")
        return ()

    if still is not None:
        draw(int(n_frames * still))
        return fig
    anim = FuncAnimation(fig, draw, frames=n_frames + hold, interval=1000 / ctx.fps)
    return _export(fig, anim, out, ctx, progress)


# ---------------------------------------------------------------------------
# 4. Growing bar comparison
# ---------------------------------------------------------------------------
def _bar_rows(cfg):
    """Either manual label/value rows, or a metric computed per ticker."""
    rows = cfg.get("rows") or []
    if rows:
        clean = [(str(r["label"]), float(r["value"])) for r in rows
                 if str(r.get("label", "")).strip() != "" and r.get("value") not in (None, "")]
        if clean:
            return clean, cfg.get("unit", ""), int(cfg.get("decimals", 1))

    metric = cfg.get("metric", "return")
    frames = datasrc.fetch_many(cfg["tickers"], **datasrc.window(cfg))
    periods = datasrc.periods_per_year(_interval(cfg))
    out = []
    for tk, df in frames.items():
        cl = df["Close"].to_numpy(float)
        if metric == "return":
            out.append((tk, (cl[-1] / cl[0] - 1) * 100))
        elif metric == "drawdown":
            peak = np.maximum.accumulate(cl)
            out.append((tk, ((cl / peak - 1) * 100).min()))
        elif metric == "volatility":
            r = np.diff(np.log(cl))
            out.append((tk, r.std() * np.sqrt(periods) * 100))
        else:
            out.append((tk, cl[-1]))
    unit = "$" if metric == "price" else "%"
    return out, unit, 1


def render_bars(cfg, ctx, out, progress=None, still=None):
    rows, unit, dec = _bar_rows(cfg)
    rows.sort(key=lambda r: r[1], reverse=True)
    labels = [r[0] for r in rows]
    finals = np.array([r[1] for r in rows], float)
    n = len(rows)

    n_frames = max(int(cfg["duration"] * ctx.fps), 2)
    hold = int(cfg["hold"] * ctx.fps)
    stagger = 0.35  # fraction of the reveal spent staggering bar starts

    t = ctx.theme
    fig = _new_fig(ctx)
    _titles(fig, ctx, cfg.get("title") or "Comparison",
            cfg.get("subtitle") or "", cfg.get("footer"))
    rect = _plot_area(ctx, True)
    ax = fig.add_axes([rect[0] + 0.04, rect[1], rect[2] - 0.02, rect[3]])
    ax.set_facecolor(ctx.bg)
    for side in ("top", "right", "bottom"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color(t["axis"])
    ax.grid(True, axis="x", color=t["grid"], linewidth=0.8 * ctx.s)
    ax.set_axisbelow(True)
    ax.tick_params(colors=t["muted"], labelsize=16 * ctx.s, length=0)
    ax.set_xticks([])

    ypos = np.arange(n)[::-1]
    ax.set_yticks(ypos)
    ax.set_yticklabels(labels, fontsize=18 * ctx.s, color=t["text"],
                       fontweight="bold")
    lo = min(0.0, finals.min() * 1.55)
    hi = max(0.0, finals.max() * 1.15)
    span = hi - lo or 1.0
    ax.set_xlim(lo, hi + span * 0.12)
    ax.set_ylim(-0.7, n - 0.3)
    if lo < 0:
        ax.spines["left"].set_visible(False)
        ax.axvline(0, color=t["axis"], lw=1.4 * ctx.s, zorder=2)

    palette = t["series"]
    use_signed = cfg.get("color_by_sign", True) and (finals.min() < 0)
    bars, texts = [], []
    for i, (lab, v) in enumerate(rows):
        col = (t["up"] if v >= 0 else t["down"]) if use_signed else palette[i % len(palette)]
        b = ax.barh(ypos[i], 0, height=0.55, color=col, zorder=3)[0]
        bars.append(b)
        texts.append(ax.text(0, ypos[i], "", color=col, fontsize=19 * ctx.s,
                             fontweight="bold", va="center", ha="left",
                             fontfamily=MONO_STACK, zorder=4))

    def fmt(v):
        if unit == "$":
            return _money(v)
        return f"{v:,.{dec}f}{unit}"

    def draw(i):
        gt = min(i / max(n_frames - 1, 1), 1.0)
        for j, (b, txt) in enumerate(zip(bars, texts)):
            start = (j / max(n - 1, 1)) * stagger
            local = 0.0 if gt <= start else (gt - start) / max(1 - stagger, 1e-6)
            v = finals[j] * float(ease(cfg["easing"], min(local, 1.0)))
            b.set_width(v)
            anchor = v if finals[j] >= 0 else 0.0
            txt.set_position((anchor + span * 0.015, ypos[j]))
            txt.set_text(fmt(v))
        return ()

    if still is not None:
        draw(int(n_frames * still))
        return fig
    anim = FuncAnimation(fig, draw, frames=n_frames + hold, interval=1000 / ctx.fps)
    return _export(fig, anim, out, ctx, progress)


# ---------------------------------------------------------------------------
# 5. Annotated timeline
# ---------------------------------------------------------------------------
# Which callouts get first claim on the space. A typed one outranks everything: it is the
# thing the feed couldn't know, and it is why someone opened the field. Below it, a label
# that says something specific beats one that repeats — every earnings mark on a chart reads
# "Earnings", so an earnings label is the one worth losing when two collide.
CALLOUT_RANK = {"manual": 0, "splits": 1, "dividends": 2, "earnings": 3}

CALLOUT_ROWS = 3       # heights a label can stack into before it goes unlabelled
CALLOUT_BASE = 0.115   # the first row, as a share of the frame height above its point
CALLOUT_STEP = 0.105   # and the gap up to the next
CALLOUT_TICK = 0.045   # stem length for a mark that didn't get a label
# A character's width as a share of the font size. Measured across the labels these charts
# actually carry, the real figure runs 0.41–0.51 for anything long enough to collide, so
# this over-estimates on purpose: too wide costs one label that would have fitted, too
# narrow costs an overlap. Short labels are the other way (a two-character one measures
# 0.67) but a shortfall of 0.003 of the axis disappears inside the gap below.
_CHAR_W = 0.55
_CALLOUT_GAP = 0.014   # clear space between two labels, as a share of the axis width


def plan_callouts(notes, xlim, ylim, log, ctx, fontsize, rect):
    """Give each callout a row and a text anchor, or no label when there is no room.

    Sets `row`, `ha` and `frac` on each note in place, `row=None` meaning the mark is drawn
    without its text. **Nothing is ever dropped** — the dot and stem go on every event, and
    only the label thins out. That distinction is what makes the auto-annotations honest: a
    chart showing four of a year's eight earnings dates would read as the complete set, and
    since every one of those labels says the same word, losing some of the text loses no
    information at all.

    The test is against a rectangle rather than a column, because a row is a lift above each
    callout's *own* point rather than a shared height. Two labels a row apart whose prices
    differ by that same lift land on exactly the same line — which is what a purely
    horizontal check misses, and it is the collision that actually shows up on a chart.

    Planned once against the resting frame rather than per frame, for the same reason the
    camera is: `still=` asks for frame 200 without drawing the 199 before it. A layout that
    re-solved itself as the camera changed the visible span would also make labels jump rows
    mid-move, which reads as a bug rather than as a camera.

    Label widths are estimated from character counts rather than measured, because measuring
    needs a draw and this has to settle before the first frame. `ctx.s` cancels out of the
    arithmetic — a 720p draft puts the same share of the frame under a label as a 1080p
    final does, which is the whole job of the scale factor.
    """
    x0, x1 = xlim
    y0, y1 = ylim
    span = (x1 - x0) or 1.0
    axis_w = ctx.w / ctx.dpi * 72.0 * rect[2]
    axis_h = ctx.h / ctx.dpi * 72.0 * rect[3]
    line_h = 1.3 * fontsize / axis_h

    def height(v):
        """Where a price sits in the resting frame — 0 at the bottom edge, 1 at the top."""
        if log:
            return float(np.log(v / y0) / (np.log(y1 / y0) or 1.0))
        return float((v - y0) / ((y1 - y0) or 1.0))

    placed = []
    for note in sorted(notes, key=lambda n: (CALLOUT_RANK.get(n["kind"], 9), n["x"])):
        pos = (note["x"] - x0) / span
        w = _CHAR_W * fontsize * len(note["label"]) / axis_w + _CALLOUT_GAP
        # Anchored away from whichever edge it would otherwise cross, so the first and last
        # callout on a chart stay inside the frame instead of running off it.
        if pos - w / 2 < 0:
            note["ha"], lo = "left", pos
        elif pos + w / 2 > 1:
            note["ha"], lo = "right", pos - w
        else:
            note["ha"], lo = "center", pos - w / 2

        base = height(note["y"])
        note["row"] = None
        for r in range(CALLOUT_ROWS):
            bottom = base + CALLOUT_BASE + CALLOUT_STEP * r
            # Rows only go up, so the first one that leaves the frame rules out the rest.
            # A callout sitting at the top of the range keeps row zero and nothing above it.
            if bottom + line_h > 1.0:
                break
            box = (lo, lo + w, bottom, bottom + line_h)
            if any(not (box[1] <= o[0] or box[0] >= o[1]
                        or box[3] <= o[2] or box[2] >= o[3]) for o in placed):
                continue
            placed.append(box)
            note["row"] = r
            break
        note["frac"] = (CALLOUT_TICK if note["row"] is None
                        else CALLOUT_BASE + CALLOUT_STEP * note["row"])
    return notes


def _note_x(date, df, x, intraday):
    """Where a date lands on the axis, or None when it isn't on this chart.

    On a positional axis a callout has to land on the bar it refers to, so the timestamp
    resolves to the nearest one rather than to a coordinate.
    """
    try:
        ts = pd.Timestamp(date)
    except (ValueError, TypeError):
        return None
    if pd.isna(ts):
        return None
    d = (float(df.index.get_indexer([ts], method="nearest")[0]) if intraday
         else mdates.date2num(ts.to_pydatetime()))
    return d if x[0] <= d <= x[-1] else None


def timeline_notes(cfg, df, x, y, intraday):
    """Every callout the chart will carry — looked up and typed — resolved onto the axis.

    The auto events are fetched first and a typed callout on the same bar replaces one,
    rather than landing on top of it. Two labels on one date is the one collision the layout
    cannot solve, and the field exists to say what the feed can't, so the typed one wins.

    A failed lookup costs the marks and nothing else: `datasrc.events` swallows it and this
    still returns whatever was typed. The alternative is failing a render over an overlay,
    which trades a recoverable outcome for one that isn't.
    """
    win = datasrc.window(cfg)
    found = datasrc.events(cfg["tickers"][0], win["start"], win.get("end"),
                           cfg.get("auto_annotations") or [])

    notes = {}
    for row in found:
        d = _note_x(row["date"], df, x, intraday)
        if d is not None:
            notes[d] = {"x": d, "label": row["label"], "kind": row["kind"]}
    for a in cfg.get("annotations", []):
        label = str(a.get("label", "")).strip()
        if not a.get("date") or not label:
            continue
        d = _note_x(a["date"], df, x, intraday)
        if d is not None:
            notes[d] = {"x": d, "label": label, "kind": "manual"}

    for note in notes.values():
        note["y"] = float(np.interp(note["x"], x, y))
    return sorted(notes.values(), key=lambda n: n["x"])


def render_timeline(cfg, ctx, out, progress=None, still=None):
    tk = cfg["tickers"][0]
    intraday = _intraday(cfg)
    periods = cfg.get("ma") or []
    df, ma_series = _fetch_with_ma(tk, cfg, periods)
    x = _x_values(df.index, intraday)
    y = df["Close"].to_numpy(float)

    notes = timeline_notes(cfg, df, x, y, intraday)

    dense_n = max(int(cfg["duration"] * ctx.fps) * 2, 2000)
    xd, yd = _densify(x, y, dense_n)
    n_frames, hold, cut, _ = _plan(cfg["duration"], cfg["hold"], ctx.fps,
                                   cfg["easing"], dense_n)

    mas = _align_ma(ma_series, df.index)
    ma_vals = [(p, _dense_ma(x, mas[p], xd)) for p in periods]
    ma_cut = ma_track(cfg, cut, n_frames, ctx.fps)
    lo, hi = _extend_range(y.min(), y.max(), [v for _, v in ma_vals])
    log = _log_ok(cfg, lo)

    t = ctx.theme
    rising = y[-1] >= y[0]
    color = t["up"] if rising else t["down"]
    fig = _new_fig(ctx)
    pct = (y[-1] / y[0] - 1) * 100
    _titles(fig, ctx, cfg.get("title") or tk,
            cfg.get("subtitle")
            or f"{_range_label(df.index, intraday)}   ·   {pct:+.1f}%{_scale_note(log)}",
            cfg.get("footer"))
    rect = _plot_area(ctx, True)
    ax = fig.add_axes(rect)
    _style_axes(ax, ctx, y_fmt=_money_axis(y.min(), y.max()), x_dates=not intraday,
                log=log)

    # A chart carrying callouts makes room for them, because they lift off their own points
    # and the top row would otherwise land outside the frame. Keyed off whether there are
    # any rather than off how they end up stacked: the layout is solved *against* this
    # frame, so sizing the frame from the layout would be circular — and a timeline with no
    # callouts keeps exactly the framing it has always had.
    note_size = 15 * ctx.s
    pad = (y.max() - y.min()) * 0.18
    rest_y = (y.min() - pad * 0.6, y.max() + pad * (1.45 if notes else 1.0))
    plan_callouts(notes, (x[0], x[-1]), rest_y, log, ctx, note_size, rect)
    cam = Camera(cfg, ctx, x=xd, lo=yd, hi=yd, head=head_track(xd, cut, hold),
                 n_frames=n_frames, hold_frames=hold, rest_y=rest_y, log=log)
    cam.apply(ax, 0)
    if intraday:
        _position_ticks(ax, ctx, df.index, ax.get_xlim())

    def lift_to(base, frac, i):
        """Move `base` up by `frac` of frame `i`'s height.

        A log axis makes a fixed offset in price a different visual distance depending on
        where on the scale you are, so the same fraction has to become a ratio instead.
        Read per frame because the camera changes the height underneath it.
        """
        if log:
            return base * (cam.y1[i] / cam.y0[i]) ** frac
        return base + cam.height(i) * frac

    ma_pairs = _ma_lines(ax, ctx, ma_vals, avoid=color)
    _ma_key(ax, ctx, ma_pairs, rising=rising)
    glow = _glow(ax, color, ctx)
    (line,) = ax.plot([], [], color=color, lw=2.6 * ctx.s, solid_capstyle="round",
                      zorder=4)
    (head,) = ax.plot([], [], "o", color=color, markersize=9 * ctx.s,
                      markeredgecolor=ctx.bg, markeredgewidth=2 * ctx.s, zorder=5)
    fill = [None]

    marks = []
    for note in notes:
        # Both from the note rather than re-derived: the planner solved the layout against
        # this exact price, so a second interpolation here is a second chance to disagree.
        d, yv = note["x"], note["y"]
        vl = ax.plot([d, d], [yv, yv], color=t["muted"], lw=1.2 * ctx.s,
                     ls=(0, (3, 3)), alpha=0, zorder=3)[0]
        dot = ax.plot([d], [yv], "o", color=t["text"], markersize=6 * ctx.s,
                      alpha=0, zorder=5)[0]
        # An unlabelled mark still gets its dot and a short stem. It is the one on a date
        # too crowded to write on, not one the chart decided to leave out.
        txt = (ax.text(d, yv, note["label"], color=t["text"], fontsize=note_size,
                       ha=note["ha"], va="bottom", alpha=0, zorder=6)
               if note["row"] is not None else None)
        # Frame at which the reveal head first crosses this date.
        trigger = int(np.searchsorted(cut, np.searchsorted(xd, d)))
        marks.append({"x": d, "y": yv, "frac": note["frac"], "vl": vl, "dot": dot,
                      "txt": txt, "trigger": trigger})

    fade_frames = max(int(0.28 * ctx.fps), 6)

    def draw(i):
        i = cam.apply(ax, i)
        k = cut[min(i, n_frames - 1)]
        xs, ys = xd[:k], yd[:k]
        line.set_data(xs, ys)
        for g in glow:
            g.set_data(xs, ys)
        km = ma_cut[min(i, n_frames - 1)]
        for ln, vals in ma_pairs:
            ln.set_data(xd[:km], vals[:km])
        head.set_data([xs[-1]], [ys[-1]])
        if fill[0] is not None:
            fill[0].remove()
        fill[0] = ax.fill_between(xs, cam.bottom(i), ys, color=color, alpha=0.10,
                                  linewidth=0, zorder=1)
        # Callout heights are a share of the frame rather than of the price range, so a
        # camera that changes what a dollar is worth in pixels doesn't leave the labels
        # stranded halfway up the chart or shoved off the top of it.
        for m in marks:
            if i < m["trigger"]:
                continue
            a = float(ease("out", min((i - m["trigger"]) / fade_frames, 1.0)))
            frac = m["frac"]
            m["vl"].set_data([m["x"], m["x"]], [m["y"], lift_to(m["y"], frac, i)])
            m["vl"].set_alpha(a * 0.9)
            m["dot"].set_alpha(a)
            if m["txt"] is not None:
                m["txt"].set_alpha(a)
                m["txt"].set_position((m["x"],
                                       lift_to(m["y"], frac - 0.03 * (1 - a), i)))
        if cam.moving:
            # A date axis re-formats; a positional one re-labels. Same job either way —
            # the camera changed the visible span underneath the ticks.
            if intraday:
                _position_ticks(ax, ctx, df.index, ax.get_xlim())
            else:
                _date_ticks(ax, cam.width(i))
        return ()

    if still is not None:
        draw(int(n_frames * still))
        return fig
    anim = FuncAnimation(fig, draw, frames=n_frames + hold, interval=1000 / ctx.fps)
    return _export(fig, anim, out, ctx, progress)


# ---------------------------------------------------------------------------
# 6. Bar chart race
# ---------------------------------------------------------------------------
def render_race(cfg, ctx, out, progress=None, still=None):
    intraday = _intraday(cfg)
    frames = datasrc.fetch_many(cfg["tickers"], **datasrc.window(cfg))
    closes = pd.DataFrame({k: v["Close"] for k, v in frames.items()}).dropna()
    if closes.shape[1] < 2:
        raise ValueError("A race needs at least two tickers.")

    normalize = cfg.get("normalize", True)
    vals = closes / closes.iloc[0] * 100 if normalize else closes
    names = list(vals.columns)
    x = _x_values(closes.index, intraday)

    dense_n = max(int(cfg["duration"] * ctx.fps), 600)
    xd = np.linspace(x[0], x[-1], dense_n)
    mat = np.vstack([np.interp(xd, x, vals[c].to_numpy(float)) for c in names])
    n_frames, hold, cut, _ = _plan(cfg["duration"], cfg["hold"], ctx.fps,
                                   "linear", dense_n)

    t = ctx.theme
    n = len(names)
    fig = _new_fig(ctx)
    _titles(fig, ctx, cfg.get("title") or "Performance race",
            cfg.get("subtitle") or ("Indexed to 100" if normalize else "Closing price"),
            cfg.get("footer"))
    rect = _plot_area(ctx, True)
    ax = fig.add_axes([rect[0] + 0.05, rect[1], rect[2] - 0.03, rect[3]])
    ax.set_facecolor(ctx.bg)
    for side in ("top", "right", "bottom", "left"):
        ax.spines[side].set_visible(False)
    ax.grid(True, axis="x", color=t["grid"], linewidth=0.8 * ctx.s)
    ax.set_axisbelow(True)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlim(0, mat.max() * 1.18)
    ax.set_ylim(-0.7, n - 0.3)

    palette = t["series"]
    colors = {c: palette[i % len(palette)] for i, c in enumerate(names)}
    bars, name_txt, val_txt = {}, {}, {}
    for c in names:
        bars[c] = ax.barh(0, 0, height=0.62, color=colors[c], zorder=3)[0]
        name_txt[c] = ax.text(0, 0, c, color=t["text"], fontsize=18 * ctx.s,
                              fontweight="bold", va="center", ha="right", zorder=4)
        val_txt[c] = ax.text(0, 0, "", color=colors[c], fontsize=17 * ctx.s,
                             fontweight="bold", va="center", ha="left",
                             fontfamily=MONO_STACK, zorder=4)
    clock = fig.text(0.90, 0.16, "", color=t["muted"], fontsize=30 * ctx.s,
                     ha="right", va="center", fontfamily=MONO_STACK, alpha=0.55)

    pos = np.arange(n, dtype=float)[::-1]  # smoothed row positions
    smooth = 1.0 - float(np.exp(-11.0 / ctx.fps))
    xmax = mat.max() * 1.18

    def draw(i):
        nonlocal pos
        k = cut[min(i, n_frames - 1)]
        cur = mat[:, k]
        order = np.argsort(-cur)
        target = np.empty(n, float)
        for rank, j in enumerate(order):
            target[j] = n - 1 - rank
        pos = pos + (target - pos) * smooth
        for j, c in enumerate(names):
            bars[c].set_y(pos[j] - 0.31)
            bars[c].set_width(cur[j])
            name_txt[c].set_position((-xmax * 0.012, pos[j]))
            val_txt[c].set_position((cur[j] + xmax * 0.012, pos[j]))
            val_txt[c].set_text(f"{cur[j]:,.0f}" if normalize else _money(cur[j]))
        moment = (_at_position(closes.index, xd[k]) if intraday
                  else mdates.num2date(xd[k]))
        clock.set_text(_stamp(moment, closes.index, intraday, "%b %Y"))
        return ()

    if still is not None:
        for f in range(0, int(n_frames * still), 3):
            draw(f)
        return fig
    anim = FuncAnimation(fig, draw, frames=n_frames + hold, interval=1000 / ctx.fps)
    return _export(fig, anim, out, ctx, progress)


CHARTS = {
    "line": {"fn": render_line, "label": "Line reveal", "tickers": 1,
             "desc": "One ticker drawing left to right with a live price readout."},
    "compare": {"fn": render_compare, "label": "Comparison", "tickers": 6,
                "desc": "Several tickers indexed to 100, labelled at the line ends."},
    "candles": {"fn": render_candles, "label": "Candlesticks", "tickers": 1,
                "desc": "OHLC candles appearing in sequence over a volume strip."},
    "bars": {"fn": render_bars, "label": "Bar comparison", "tickers": 8,
             "desc": "Bars growing to a metric or your own numbers, staggered."},
    "timeline": {"fn": render_timeline, "label": "Annotated timeline", "tickers": 1,
                 "desc": "A line reveal with callouts landing on dates you set."},
    "race": {"fn": render_race, "label": "Bar race", "tickers": 8,
             "desc": "Ranked bars reordering as performance changes over time."},
}


def render(cfg, out_path, progress=None):
    kind = cfg.get("chart", "line")
    if kind not in CHARTS:
        raise ValueError(f"Unknown chart type: {kind}")
    datasrc.reset_sources()
    ctx = make_ctx(cfg.get("theme", "midnight"), cfg.get("aspect", "16:9"),
                   cfg.get("quality", "final"), cfg.get("fps"), cfg.get("resolution"),
                   cfg.get("transparent", False), cfg.get("preset"))
    return CHARTS[kind]["fn"](cfg, ctx, out_path, progress=progress)


def save_still(cfg, fileobj, at=0.72, quality="draft", dpi=None, res=None):
    """Write one frame of the animation to `fileobj` as a PNG.

    The live preview and the thumbnail export are this same call at two sizes, so the
    still is always exactly the frame the video would have shown at that point — which is
    the whole reason it works as a thumbnail. Returns the Ctx so the caller can report the
    dimensions it actually got.

    `res` is what makes "same size as the video" true now that the slate sets resolution
    independently of the quality tier: the thumbnail export passes it, the preview leaves
    it unset and stays at draft size to keep the base64 payload small.
    """
    kind = cfg.get("chart", "line")
    if kind not in CHARTS:
        raise ValueError(f"Unknown chart type: {kind}")
    # Reset here too, so the preview footer matches what the render will produce.
    datasrc.reset_sources()
    ctx = make_ctx(cfg.get("theme", "midnight"), cfg.get("aspect", "16:9"), quality,
                   res=res, transparent=cfg.get("transparent", False))
    fig = CHARTS[kind]["fn"](cfg, ctx, None, still=at)
    try:
        fig.savefig(fileobj, format="png", dpi=dpi or ctx.dpi, facecolor=ctx.bg)
    finally:
        plt.close(fig)
    return ctx

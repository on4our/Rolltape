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

FONT_STACK = ["Inter", "Helvetica Neue", "Arial", "Liberation Sans", "DejaVu Sans"]
MONO_STACK = ["JetBrains Mono", "SF Mono", "Menlo", "Consolas", "DejaVu Sans Mono"]

# h264 has no alpha channel, so a transparent render changes codec and container both.
# ProRes 4444 is the intermediate every NLE ingests without a transcode, and prores_ks is
# a native FFmpeg encoder — present even in the static build the serverless bundle ships,
# where libvpx usually isn't.
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


def make_ctx(theme_name, aspect, quality, fps=None, res=None, transparent=False) -> Ctx:
    theme = THEMES.get(theme_name, THEMES["midnight"])
    enc = ENCODE.get(quality, ENCODE["final"])
    sizes = SIZES.get(aspect, SIZES["16:9"])
    w, h = sizes.get(res or enc["res"], sizes[enc["res"]])
    return Ctx(theme=theme, w=w, h=h, fps=int(fps or enc["fps"]),
               crf=enc["crf"], preset=enc["preset"], transparent=bool(transparent))


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


def _style_axes(ax, ctx, y_fmt=None, x_dates=True):
    t = ctx.theme
    ax.set_facecolor(ctx.bg)
    ax.grid(True, color=t["grid"], linewidth=0.8 * ctx.s, alpha=0.9)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(t["axis"])
    ax.tick_params(colors=t["muted"], labelsize=13 * ctx.s, length=0)
    for lbl in ax.get_xticklabels() + ax.get_yticklabels():
        lbl.set_fontfamily(MONO_STACK)
    if x_dates:
        ax.xaxis.set_major_locator(
            mdates.AutoDateLocator(minticks=3, maxticks=5 if ctx.tall else 8))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    if y_fmt:
        ax.yaxis.set_major_formatter(y_fmt)


def _money(v, _=None):
    a = abs(v)
    if a >= 100:
        return f"${v:,.0f}"
    return f"${v:,.2f}" if a < 10 else f"${v:,.1f}"


def _glow(ax, color, ctx, zorder=2):
    return [ax.plot([], [], color=color, lw=w * ctx.s, alpha=a,
                    solid_capstyle="round", zorder=zorder)[0]
            for w, a in ((11, 0.05), (7, 0.09), (4.5, 0.15))]


_FFMPEG_CHECKED = False


def _resolve_ffmpeg():
    """Point matplotlib at a bundled ffmpeg when the system has none.

    Locally ffmpeg is on PATH and imageio_ffmpeg is never imported. Serverless runtimes
    ship no ffmpeg binary at all, so the pip-installed static build is the only option.
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
    df = datasrc.fetch(tk, cfg["start"], cfg.get("end"))
    x = mdates.date2num(df.index.to_pydatetime())
    y = df["Close"].to_numpy(float)

    dense_n = max(int(cfg["duration"] * ctx.fps) * 2, 2000)
    xd, yd = _densify(x, y, dense_n)
    n_frames, hold, cut, _ = _plan(cfg["duration"], cfg["hold"], ctx.fps,
                                   cfg["easing"], dense_n)

    t = ctx.theme
    up = y[-1] >= y[0]
    color = t["up"] if up else t["down"]
    pct = (y[-1] / y[0] - 1) * 100

    fig = _new_fig(ctx)
    _titles(fig, ctx, cfg.get("title") or tk,
            cfg.get("subtitle") or f"{df.index[0]:%b %Y} – {df.index[-1]:%b %Y}   ·   {pct:+.1f}%",
            cfg.get("footer"))
    ax = fig.add_axes(_plot_area(ctx, True))
    _style_axes(ax, ctx, y_fmt=_money)

    pad = (y.max() - y.min()) * 0.12 or y.max() * 0.05
    ax.set_xlim(x[0], x[-1])
    ax.set_ylim(y.min() - pad, y.max() + pad)
    floor = ax.get_ylim()[0]

    glow = _glow(ax, color, ctx)
    (line,) = ax.plot([], [], color=color, lw=2.6 * ctx.s, solid_capstyle="round",
                      solid_joinstyle="round", zorder=4)
    (head,) = ax.plot([], [], "o", color=color, markersize=9 * ctx.s,
                      markeredgecolor=ctx.bg, markeredgewidth=2 * ctx.s, zorder=5)
    readout = ax.text(0, 0, "", color=t["text"], fontsize=22 * ctx.s,
                      fontweight="bold", ha="left", va="center", zorder=6,
                      fontfamily=MONO_STACK)
    fill = [None]
    span = x[-1] - x[0]

    def draw(i):
        k = cut[min(i, n_frames - 1)]
        xs, ys = xd[:k], yd[:k]
        line.set_data(xs, ys)
        for g in glow:
            g.set_data(xs, ys)
        head.set_data([xs[-1]], [ys[-1]])
        if fill[0] is not None:
            fill[0].remove()
        fill[0] = ax.fill_between(xs, floor, ys, color=color, alpha=0.10,
                                  linewidth=0, zorder=1)
        readout.set_position((xs[-1] + span * 0.012, ys[-1]))
        readout.set_text(_money(ys[-1]))
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
    frames = datasrc.fetch_many(cfg["tickers"], cfg["start"], cfg.get("end"))
    closes = pd.DataFrame({k: v["Close"] for k, v in frames.items()}).dropna()
    if closes.empty:
        raise ValueError("Tickers have no overlapping trading days.")

    normalize = cfg.get("normalize", True)
    vals = closes / closes.iloc[0] * 100 if normalize else closes
    x = mdates.date2num(closes.index.to_pydatetime())

    dense_n = max(int(cfg["duration"] * ctx.fps) * 2, 2000)
    xd = np.linspace(x[0], x[-1], dense_n)
    series = {c: np.interp(xd, x, vals[c].to_numpy(float)) for c in vals.columns}
    n_frames, hold, cut, _ = _plan(cfg["duration"], cfg["hold"], ctx.fps,
                                   cfg["easing"], dense_n)

    t = ctx.theme
    palette = t["series"]
    fig = _new_fig(ctx)
    sub = ("Indexed to 100" if normalize else "Closing price") + \
          f"   ·   {closes.index[0]:%b %Y} – {closes.index[-1]:%b %Y}"
    _titles(fig, ctx, cfg.get("title") or " vs ".join(vals.columns),
            cfg.get("subtitle") or sub, cfg.get("footer"))
    ax = fig.add_axes(_plot_area(ctx, True))
    _style_axes(ax, ctx, y_fmt=(lambda v, _: f"{v:,.0f}") if normalize else _money)

    allv = np.concatenate(list(series.values()))
    pad = (allv.max() - allv.min()) * 0.12
    ax.set_xlim(x[0], x[-1])
    ax.set_ylim(allv.min() - pad, allv.max() + pad)
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
    span = x[-1] - x[0]

    def draw(i):
        k = cut[min(i, n_frames - 1)]
        # Nudge labels apart so overlapping series stay readable.
        ends = sorted(((series[c][k - 1], c) for c in series), reverse=True)
        min_gap = (ax.get_ylim()[1] - ax.get_ylim()[0]) * 0.045
        placed = []
        for v, c in ends:
            pos = v if not placed else min(v, placed[-1] - min_gap)
            placed.append(pos)
            lines[c].set_data(xd[:k], series[c][:k])
            labels[c].set_position((x[-1] + span * 0.015, pos))
            labels[c].set_text(f"{c} {v:,.0f}" if normalize else f"{c} {_money(v)}")
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
    df = datasrc.fetch(tk, cfg["start"], cfg.get("end"))
    max_c = int(cfg.get("max_candles", 90))
    if len(df) > max_c:
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

    t = ctx.theme
    fig = _new_fig(ctx)
    pct = (c[-1] / o[0] - 1) * 100
    _titles(fig, ctx, cfg.get("title") or tk,
            cfg.get("subtitle") or f"{df.index[0]:%b %Y} – {df.index[-1]:%b %Y}   ·   {pct:+.1f}%",
            cfg.get("footer"))

    rect = _plot_area(ctx, True)
    vol_h = rect[3] * 0.20
    ax = fig.add_axes([rect[0], rect[1] + vol_h + rect[3] * 0.06,
                       rect[2], rect[3] - vol_h - rect[3] * 0.06])
    axv = fig.add_axes([rect[0], rect[1], rect[2], vol_h], sharex=ax)
    _style_axes(ax, ctx, y_fmt=_money, x_dates=False)
    _style_axes(axv, ctx, y_fmt=lambda v, _: f"{v/1e6:,.0f}M", x_dates=False)
    ax.tick_params(labelbottom=False)
    axv.grid(False)

    pad = (h.max() - l.min()) * 0.10
    ax.set_xlim(-1, n)
    ax.set_ylim(l.min() - pad, h.max() + pad)
    axv.set_ylim(0, vol.max() * 1.15)
    axv.set_yticks([vol.max()])

    step = max(n // 6, 1)
    axv.set_xticks(idx[::step])
    axv.set_xticklabels([f"{d:%b %y}" for d in df.index[::step]])
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

    bull, bear = t["up"], t["down"]
    colors = [bull if c[i] >= o[i] else bear for i in range(n)]
    w = 0.34

    def draw(i):
        k = cut[min(i, n_frames - 1)]
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
        readout.set_text(f"{_money(c[k-1])}   {df.index[k-1]:%d %b %Y}")
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
    frames = datasrc.fetch_many(cfg["tickers"], cfg["start"], cfg.get("end"))
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
            out.append((tk, r.std() * np.sqrt(252) * 100))
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
def render_timeline(cfg, ctx, out, progress=None, still=None):
    tk = cfg["tickers"][0]
    df = datasrc.fetch(tk, cfg["start"], cfg.get("end"))
    x = mdates.date2num(df.index.to_pydatetime())
    y = df["Close"].to_numpy(float)

    notes = []
    for a in cfg.get("annotations", []):
        if not a.get("date") or not str(a.get("label", "")).strip():
            continue
        try:
            d = mdates.date2num(pd.Timestamp(a["date"]).to_pydatetime())
        except Exception:  # noqa: BLE001
            continue
        if x[0] <= d <= x[-1]:
            notes.append((d, str(a["label"]).strip()))
    notes.sort()

    dense_n = max(int(cfg["duration"] * ctx.fps) * 2, 2000)
    xd, yd = _densify(x, y, dense_n)
    n_frames, hold, cut, _ = _plan(cfg["duration"], cfg["hold"], ctx.fps,
                                   cfg["easing"], dense_n)

    t = ctx.theme
    color = t["up"] if y[-1] >= y[0] else t["down"]
    fig = _new_fig(ctx)
    pct = (y[-1] / y[0] - 1) * 100
    _titles(fig, ctx, cfg.get("title") or tk,
            cfg.get("subtitle") or f"{df.index[0]:%b %Y} – {df.index[-1]:%b %Y}   ·   {pct:+.1f}%",
            cfg.get("footer"))
    ax = fig.add_axes(_plot_area(ctx, True))
    _style_axes(ax, ctx, y_fmt=_money)

    pad = (y.max() - y.min()) * 0.18
    ax.set_xlim(x[0], x[-1])
    ax.set_ylim(y.min() - pad * 0.6, y.max() + pad)
    floor = ax.get_ylim()[0]
    span_y = ax.get_ylim()[1] - floor

    glow = _glow(ax, color, ctx)
    (line,) = ax.plot([], [], color=color, lw=2.6 * ctx.s, solid_capstyle="round",
                      zorder=4)
    (head,) = ax.plot([], [], "o", color=color, markersize=9 * ctx.s,
                      markeredgecolor=ctx.bg, markeredgewidth=2 * ctx.s, zorder=5)
    fill = [None]

    marks = []
    for k, (d, lab) in enumerate(notes):
        yv = float(np.interp(d, x, y))
        row = k % 2  # alternate heights so neighbouring notes don't collide
        ly = yv + span_y * (0.14 + 0.13 * row)
        vl = ax.plot([d, d], [yv, ly], color=t["muted"], lw=1.2 * ctx.s,
                     ls=(0, (3, 3)), alpha=0, zorder=3)[0]
        dot = ax.plot([d], [yv], "o", color=t["text"], markersize=6 * ctx.s,
                      alpha=0, zorder=5)[0]
        txt = ax.text(d, ly, lab, color=t["text"], fontsize=15 * ctx.s,
                      ha="center", va="bottom", alpha=0, zorder=6)
        # Frame at which the reveal head first crosses this date.
        trigger = int(np.searchsorted(cut, np.searchsorted(xd, d)))
        marks.append({"x": d, "vl": vl, "dot": dot, "txt": txt, "y0": ly,
                      "trigger": trigger})

    fade_frames = max(int(0.28 * ctx.fps), 6)

    def draw(i):
        k = cut[min(i, n_frames - 1)]
        xs, ys = xd[:k], yd[:k]
        line.set_data(xs, ys)
        for g in glow:
            g.set_data(xs, ys)
        head.set_data([xs[-1]], [ys[-1]])
        if fill[0] is not None:
            fill[0].remove()
        fill[0] = ax.fill_between(xs, floor, ys, color=color, alpha=0.10,
                                  linewidth=0, zorder=1)
        for m in marks:
            if i < m["trigger"]:
                continue
            a = float(ease("out", min((i - m["trigger"]) / fade_frames, 1.0)))
            m["vl"].set_alpha(a * 0.9)
            m["dot"].set_alpha(a)
            m["txt"].set_alpha(a)
            m["txt"].set_position((m["x"], m["y0"] - span_y * 0.03 * (1 - a)))
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
    frames = datasrc.fetch_many(cfg["tickers"], cfg["start"], cfg.get("end"))
    closes = pd.DataFrame({k: v["Close"] for k, v in frames.items()}).dropna()
    if closes.shape[1] < 2:
        raise ValueError("A race needs at least two tickers.")

    normalize = cfg.get("normalize", True)
    vals = closes / closes.iloc[0] * 100 if normalize else closes
    names = list(vals.columns)
    x = mdates.date2num(closes.index.to_pydatetime())

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
        clock.set_text(f"{mdates.num2date(xd[k]):%b %Y}")
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
                   cfg.get("transparent", False))
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

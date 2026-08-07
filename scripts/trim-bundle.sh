#!/usr/bin/env sh
# Trim installed packages to fit Vercel's function size ceiling.
#
# A stock install of matplotlib + pandas + numpy + a bundled ffmpeg measures ~253MB
# against a 225MB limit. Three cuts, none of which touch anything reachable at runtime,
# bring it to ~202MB. Verified by rendering line, candlestick and comparison charts on a
# trimmed install.
#
# Not a cut: fontTools. matplotlib's dviread imports fontTools.agl at module level, so
# removing it breaks rendering outright — tested, not assumed.
set -e

SP=$(python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
[ -d "$SP" ] || { echo "trim-bundle: no site-packages at $SP, skipping"; exit 0; }

before=$(du -sk "$SP" | cut -f1)

# Package test suites: ~27MB, and nothing imports them. Directories named "testing" are
# deliberately left alone — numpy.testing and pandas._testing are public API.
find "$SP" -type d -name tests -prune -exec rm -rf {} + 2>/dev/null || true

# Type stubs are for type checkers, not the interpreter.
find "$SP" -name '*.pyi' -delete 2>/dev/null || true

# Fonts for the PostScript and PDF backends. This renders through Agg into ffmpeg and
# never loads either. The STIX fonts are NOT touched: matplotlib parses paired $ as
# mathtext, and titles in a stock chart tool routinely contain dollar amounts.
rm -rf "$SP/matplotlib/mpl-data/fonts/afm" "$SP/matplotlib/mpl-data/fonts/pdfcorefonts"

# Installed-package metadata. Nothing here reads a version at runtime.
find "$SP" -maxdepth 1 -name '*.dist-info' -exec rm -rf {} + 2>/dev/null || true

# Debug symbols in the compiled extensions, worth ~23MB. Vercel's build image has no
# strip, so this is a bonus when it lands rather than something to rely on — the cuts
# above are what have to fit the budget.
if command -v strip >/dev/null 2>&1; then
    find "$SP" -name '*.so*' -exec strip --strip-unneeded {} + 2>/dev/null || true
    echo "trim-bundle: stripped debug symbols"
else
    echo "trim-bundle: no strip in this image, keeping debug symbols"
fi

find "$SP" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

after=$(du -sk "$SP" | cut -f1)
echo "trim-bundle: $((before / 1024))MB -> $((after / 1024))MB"

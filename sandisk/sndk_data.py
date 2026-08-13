"""SanDisk (NASDAQ: SNDK) figures for the slideshow and the animated charts.

Every number here is a *reported* figure from a Sandisk earnings release unless the
comment above it says otherwise. Nothing is modelled, smoothed or filled in. That rule
matters more than usual for this file: it feeds a chart that ends up in a video, and a
figure nobody can check is worse than a chart that doesn't get made. Where a value is
derived, the derivation is one line of arithmetic over reported figures and it says so.

Fiscal year: Sandisk's FY2026 ran to 3 July 2026, so FY2026 is very nearly calendar
H2-2025 plus H1-2026. Quarter end dates are given where a release stated them.

Two things deliberately NOT in this file:

- A daily price series. Sourcing one was not possible in the environment this was built
  in, and inventing one to animate would be the exact failure the renderer's own rules
  exist to prevent. Price appears here only as individually-sourced milestones.
- GAAP cost and expense lines. The margin figures published in the summaries are the
  non-GAAP ones; subtracting a non-GAAP margin from GAAP revenue to reach a cost line
  would produce a bridge that closes arithmetically and means nothing. The revenue and
  margin bridges below are built only from figures reported on the same basis.
"""

CURRENCY = "$"
UNITS = "millions"

# --- the company -----------------------------------------------------------
COMPANY = {
    "name": "Sandisk Corporation",
    "ticker": "SNDK",
    "exchange": "NASDAQ",
    "hq": "Milpitas, California",
    "ceo": "David Goeckeler (Chairman & CEO)",
    "employees": "10,000+",
    "fy2026_ended": "3 July 2026",
    # Separated from Western Digital in February 2025; regular-way trading from 24 Feb 2025.
    "spinoff": "Spun out of Western Digital, February 2025",
}

# --- FY2026 quarterly results ----------------------------------------------
# revenue / net income in $M, EPS in $, gross margin in % (non-GAAP).
# Q3 did not have a non-GAAP EPS figure in the summaries used, hence None.
QUARTERS = [
    {"label": "Q1 FY26", "ended": "2025-10-03", "revenue": 2310, "gaap_ni": 112,
     "gaap_eps": 0.75, "nongaap_eps": 1.22, "gross_margin": 29.9},
    {"label": "Q2 FY26", "ended": "2026-01-02", "revenue": 3030, "gaap_ni": 803,
     "gaap_eps": 5.15, "nongaap_eps": 6.20, "gross_margin": 51.1},
    {"label": "Q3 FY26", "ended": "2026-04-03", "revenue": 5950, "gaap_ni": 3615,
     "gaap_eps": 23.03, "nongaap_eps": None, "gross_margin": 78.4},
    {"label": "Q4 FY26", "ended": "2026-07-03", "revenue": 8965, "gaap_ni": 6900,
     "gaap_eps": 43.97, "nongaap_eps": 39.25, "gross_margin": 84.6},
]

# --- FY2026 full year ------------------------------------------------------
FY2026 = {"revenue": 20248, "revenue_growth_pct": 175, "gaap_ni": 11430,
          "gaap_eps": 73.76}

# FY2025 revenue, derived: the FY2026 release put revenue up 175% year over year, so the
# base is 20248 / 2.75. Cross-checks against the segment figures below to within 0.1%,
# which is why it is trusted enough to draw.
FY2025_REVENUE = round(FY2026["revenue"] / (1 + FY2026["revenue_growth_pct"] / 100))

# --- segments --------------------------------------------------------------
# Sandisk reports three: Datacenter, Edge (client/embedded) and Consumer. Both sets below
# sum to the reported totals, which is the check that they are complete.
SEGMENTS_FY2026 = [
    {"name": "Datacenter", "revenue": 5153, "yoy_pct": 437},
    {"name": "Edge", "revenue": 12160, "yoy_pct": 195},
    {"name": "Consumer", "revenue": 2935, "yoy_pct": 29},
]
SEGMENTS_Q4 = [
    {"name": "Datacenter", "revenue": 2977, "qoq_pct": 103},
    {"name": "Edge", "revenue": 5432, "qoq_pct": 48},
    {"name": "Consumer", "revenue": 556, "qoq_pct": -32},
]

# --- guidance --------------------------------------------------------------
GUIDANCE_Q1_FY27 = {"revenue_low": 10300, "revenue_high": 10800,
                    "nongaap_eps_low": 44.00, "nongaap_eps_high": 46.00}

# --- price milestones ------------------------------------------------------
# Individually sourced points, not a series. `recent_close_range` is a range on purpose:
# the sources consulted disagreed on the 12 Aug 2026 close, and picking one and printing
# it to the cent would be inventing a precision that isn't there.
PRICE = {
    "debut_date": "2025-02-24",
    "all_time_high": 2354.39,
    "all_time_high_date": "2026-06-22",
    "recent_close_range": (1271.05, 1344.29),
    "recent_close_date": "2026-08-12",
    "drawdown_from_high_pct": 30,   # "retreated more than 30%" from the June high
}

# --- valuation -------------------------------------------------------------
# As reported in mid-August 2026 summaries. Market cap and share count vary a little by
# provider, so both are ranges/approximations rather than a single printed figure.
VALUATION = {
    "forward_pe": 5.9,
    "peg": 0.14,
    "shares_out_m": 149.0,
    "market_cap_b_range": (180.7, 188.2),
    "cash_b": 4.76,
    "net_cash_b": 6.54,
    "net_cash_per_share": 43.89,
    "analyst_target_avg": 2053.5,
    "analyst_count": 23,
    "analyst_rating": "Strong Buy",
}

# --- technology ------------------------------------------------------------
TECH = {
    "bics10": "BiCS10 1Tb TLC sampling — >29 Gb/mm2, bit density up 59%",
    "hbf": "High Bandwidth Flash with SK hynix — first OCP spec, Aug 2026",
    "hbf_spec": "Up to 512GB per stack, up to 3TB/s",
    "datacenter_mix": "Datacenter ~12% of bits a year ago, 38% of the portfolio exiting FY26",
}


# --- derived series the charts draw from -----------------------------------
def revenue_bridge():
    """Quarterly revenue, as a bridge from Q1 to Q4 of FY2026.

    Every value is a reported quarterly revenue figure or the difference between two of
    them, so the bridge closes on Q4's reported revenue by construction rather than by
    plugging a residual.
    """
    rows = [{"label": QUARTERS[0]["label"], "value": QUARTERS[0]["revenue"],
             "kind": "start"}]
    for prev, cur in zip(QUARTERS, QUARTERS[1:]):
        rows.append({"label": cur["label"],
                     "value": cur["revenue"] - prev["revenue"], "kind": "delta"})
    rows.append({"label": "Q4 FY26 revenue", "value": QUARTERS[-1]["revenue"],
                 "kind": "total"})
    return rows


def margin_bridge():
    """Non-GAAP gross margin across FY2026, in percentage points.

    Percentage points add, which is what makes this a legitimate bridge — the underlying
    margins would not be. Same construction rule as the revenue bridge: each step is the
    difference between two reported figures.
    """
    rows = [{"label": QUARTERS[0]["label"], "value": QUARTERS[0]["gross_margin"],
             "kind": "start"}]
    for prev, cur in zip(QUARTERS, QUARTERS[1:]):
        rows.append({"label": cur["label"],
                     "value": round(cur["gross_margin"] - prev["gross_margin"], 1),
                     "kind": "delta"})
    rows.append({"label": "Q4 margin", "value": QUARTERS[-1]["gross_margin"],
                 "kind": "total"})
    return rows


def check():
    """Fail loudly if the reported figures stop reconciling.

    Worth having because this file is hand-transcribed from release summaries: a typo in a
    segment figure is invisible on a chart and obvious here.
    """
    problems = []

    q_sum = sum(q["revenue"] for q in QUARTERS)
    if abs(q_sum - FY2026["revenue"]) > 25:
        problems.append(f"quarters sum to {q_sum}, FY2026 reported {FY2026['revenue']}")

    ni_sum = sum(q["gaap_ni"] for q in QUARTERS)
    if abs(ni_sum - FY2026["gaap_ni"]) > 25:
        problems.append(f"quarterly net income sums to {ni_sum}, "
                        f"FY2026 reported {FY2026['gaap_ni']}")

    seg_sum = sum(s["revenue"] for s in SEGMENTS_FY2026)
    if abs(seg_sum - FY2026["revenue"]) > 25:
        problems.append(f"FY26 segments sum to {seg_sum}, reported {FY2026['revenue']}")

    q4_seg = sum(s["revenue"] for s in SEGMENTS_Q4)
    if abs(q4_seg - QUARTERS[-1]["revenue"]) > 25:
        problems.append(f"Q4 segments sum to {q4_seg}, "
                        f"reported {QUARTERS[-1]['revenue']}")

    # The independent check on the derived FY2025 base: rebuilding it from each segment's
    # own year-over-year growth should land on the same number as the headline 175%.
    seg_fy25 = sum(s["revenue"] / (1 + s["yoy_pct"] / 100) for s in SEGMENTS_FY2026)
    if abs(seg_fy25 - FY2025_REVENUE) / FY2025_REVENUE > 0.01:
        problems.append(f"FY2025 base disagrees: {seg_fy25:.0f} from segments "
                        f"vs {FY2025_REVENUE} from the headline growth rate")

    return problems


if __name__ == "__main__":
    issues = check()
    if issues:
        for line in issues:
            print("MISMATCH:", line)
        raise SystemExit(1)
    seg_fy25 = sum(s["revenue"] / (1 + s["yoy_pct"] / 100) for s in SEGMENTS_FY2026)
    print("All reported figures reconcile.")
    print(f"  FY2026 revenue        {FY2026['revenue']:>7,} $M "
          f"(quarters sum to {sum(q['revenue'] for q in QUARTERS):,})")
    print(f"  FY2026 net income     {FY2026['gaap_ni']:>7,} $M "
          f"(quarters sum to {sum(q['gaap_ni'] for q in QUARTERS):,})")
    print(f"  FY2025 revenue base   {FY2025_REVENUE:>7,} $M "
          f"(segments imply {seg_fy25:,.0f})")
    print(f"  Revenue bridge closes on {revenue_bridge()[-1]['value']:,} $M")
    print(f"  Margin bridge closes on {margin_bridge()[-1]['value']}%")

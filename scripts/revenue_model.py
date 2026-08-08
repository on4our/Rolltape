#!/usr/bin/env python3
"""Revenue model behind docs/revenue-projection.md.

Run it to regenerate every number in that doc:

    python3 scripts/revenue_model.py

Prices, caps and cost constants are lifted from docs/pricing.md and should only
change when that doc changes. The adoption inputs — SCENARIOS, CHURN,
ANNUAL_SHARE, MIX — are assumptions, and they're grouped together so they can be
argued with without touching the arithmetic. Standard library only.
"""

# ---------------------------------------------------------------- from pricing.md
PRICE = {"hobbyist": 9, "creator": 19, "studio": 49}
ANNUAL = {k: v * 10 for k, v in PRICE.items()}  # "Annual is 10x monthly"
CAP = {"hobbyist": 70, "creator": 250, "studio": 1000}  # hosted renders/month

DATA_LICENCE = 100  # doc gives $30-100/mo; model the top of the range
HOSTING_BASE = 40   # container host, once hosted renders exist
OPS_BASE = 25       # domain, Stripe, email, licence-key service

# Average hosted egress per sub per month. The doc prices a Creator maxing 250
# renders entirely on ProRes at ~$25 and a realistic MP4-heavy mix at ~$4; these
# are average-user figures well below both.
HOSTED_COST = {"hobbyist": 0.50, "creator": 2.00, "studio": 6.00}

RENDER_SECONDS = 35  # doc: 30-40s of one core for a 7.5s 1080p60 "final"

# ------------------------------------------------------------------- assumptions
SCENARIOS = {
    "Conservative": {1: 4, 2: 7, 3: 10},
    "Base":         {1: 10, 2: 18, 3: 30},
    "Optimistic":   {1: 22, 2: 45, 3: 75},
}
CHURN = 0.04         # monthly, net of the "pause, don't cancel" recovery
ANNUAL_SHARE = 0.30  # share of new subs choosing annual

# Phase 1 (months 1-6) sells the local Hobbyist app; phase 2 adds Creator once
# brand kits ship; phase 3 unlocks hosted and Studio. Straight from the pricing
# doc's sequencing section.
MIX = {
    1: {"hobbyist": 1.00, "creator": 0.00, "studio": 0.00},
    2: {"hobbyist": 0.70, "creator": 0.30, "studio": 0.00},
    3: {"hobbyist": 0.55, "creator": 0.35, "studio": 0.10},
}
MONTHS = 36


def phase(month):
    return 1 if month <= 6 else (2 if month <= 12 else 3)


def fee(amount):
    """Stripe: 2.9% + $0.30. The fixed 30c is why the $9 tier pays 6.2%."""
    return amount * 0.029 + 0.30


def project(adds, churn=CHURN, annual_share=ANNUAL_SHARE, mix=None):
    mix = mix or MIX
    subs = {t: 0.0 for t in PRICE}
    rows = []
    for m in range(1, MONTHS + 1):
        p = phase(m)
        for t in subs:
            subs[t] *= (1 - churn)
        for t, share in mix[p].items():
            subs[t] += adds[p] * share

        gross = net = hosted = 0.0
        for t, n in subs.items():
            # An annual sub pays 10 months of price per 12 months of service.
            mrr = n * PRICE[t] * (1 - annual_share + annual_share * 10 / 12)
            fees = (n * (1 - annual_share) * fee(PRICE[t])
                    + n * annual_share * fee(ANNUAL[t]) / 12)
            gross += mrr
            net += mrr - fees
            if p == 3:
                hosted += n * HOSTED_COST[t]

        fixed = OPS_BASE + (DATA_LICENCE + HOSTING_BASE if p == 3 else 0)
        rows.append({"m": m, "phase": p, "subs": sum(subs.values()),
                     "by_tier": dict(subs), "mrr": gross, "net": net,
                     "cost": hosted + fixed, "profit": net - hosted - fixed})
    return rows


def money(x):
    return f"${x:,.0f}"


def main():
    results = {n: project(a) for n, a in SCENARIOS.items()}

    print("=" * 78)
    print("PROJECTED REVENUE")
    print("=" * 78)
    for name, rows in results.items():
        print(f"\n{name}")
        print(f"  {'':14}{'Subs':>7}{'MRR':>10}{'ARR':>10}"
              f"{'Net/mo':>10}{'Cost/mo':>10}{'Profit/mo':>11}")
        for year in (1, 2, 3):
            r = rows[year * 12 - 1]
            print(f"  End of year {year} {r['subs']:>6.0f}{money(r['mrr']):>10}"
                  f"{money(r['mrr'] * 12):>10}{money(r['net']):>10}"
                  f"{money(r['cost']):>10}{money(r['profit']):>11}")
        for year in (1, 2, 3):
            window = rows[(year - 1) * 12: year * 12]
            print(f"  Year {year} revenue {money(sum(x['mrr'] for x in window)):>10}"
                  f"   profit {money(sum(x['profit'] for x in window)):>10}")

    print("\n" + "=" * 78)
    print("UNIT ECONOMICS")
    print("=" * 78)
    print(f"  {'':10}{'Price':>7}{'Net':>8}{'Fee %':>8}{'LTV':>9}")
    for t, p in PRICE.items():
        net = p - fee(p)
        print(f"  {t.title():10}{'$' + str(p):>7}{net:>8.2f}"
              f"{fee(p) / p * 100:>7.1f}%{money(net / CHURN):>9}")

    print("\n  Break-even against a $100/mo feed:")
    for t in ("hobbyist", "creator"):
        print(f"    {t.title():9} {DATA_LICENCE / PRICE[t]:>4.1f} subs gross"
              f"  |  {DATA_LICENCE / (PRICE[t] - fee(PRICE[t])):>4.1f} subs net of fees")

    print("\n" + "=" * 78)
    print("SENSITIVITY — base case, month 36 MRR")
    print("=" * 78)
    base = project(SCENARIOS["Base"])[-1]["mrr"]
    print(f"  {'Baseline':38}{money(base):>10}")
    cases = [
        ("Mix skews to Hobbyist (75/20/5)",
         dict(mix={**MIX, 3: {"hobbyist": .75, "creator": .20, "studio": .05}})),
        ("Mix skews to Creator (35/50/15)",
         dict(mix={**MIX, 3: {"hobbyist": .35, "creator": .50, "studio": .15}})),
        ("Churn 2%/mo", dict(churn=0.02)),
        ("Churn 6%/mo", dict(churn=0.06)),
        ("Churn 8%/mo", dict(churn=0.08)),
        ("Annual share 0%", dict(annual_share=0.0)),
        ("Annual share 60%", dict(annual_share=0.6)),
    ]
    for label, kwargs in cases:
        mrr = project(SCENARIOS["Base"], **kwargs)[-1]["mrr"]
        print(f"  {label:38}{money(mrr):>10}{(mrr / base - 1) * 100:>+7.0f}%")

    print("\n" + "=" * 78)
    print("RENDER CAPACITY — RENDER_LOCK serialises, Dockerfile pins --workers 1")
    print("=" * 78)
    per_month = 3600 / RENDER_SECONDS * 24 * 30
    print(f"  One worker at {RENDER_SECONDS}s/render: "
          f"{3600 / RENDER_SECONDS:.0f}/hour, {per_month:,.0f}/month flat out")
    print(f"  Practical ceiling at 30% utilisation: {per_month * .3:,.0f}/month")

    tiers = project(SCENARIOS["Base"])[-1]["by_tier"]
    print(f"\n  Base case month 36: {sum(tiers.values()):.0f} subs")
    for label, use in [("Quarter of cap used (realistic)", .25),
                       ("Half of cap used", .50),
                       ("Every sub maxes their cap", 1.0)]:
        demand = sum(tiers[t] * CAP[t] * use for t in tiers)
        print(f"    {label:34}{demand:>10,.0f} renders/mo"
              f"  -> {demand / (per_month * .3):>4.1f} workers")

    sold = sum(tiers[t] * CAP[t] for t in tiers)
    print(f"\n  Sold capacity if every seat maxed is {sold / per_month * 100:.0f}% "
          "of one worker running flat out, 24/7.")

    print("\n" + "=" * 78)
    print("PRORES TAIL — the sub-cap the pricing doc holds in reserve")
    print("=" * 78)
    creators = tiers["creator"]
    print(f"  Base case month 36: {creators:.0f} Creators, "
          f"{money(creators * PRICE['creator'])} revenue/mo")
    for label, per in [("Realistic MP4-heavy mix (~$4)", 4.0),
                       ("All-ProRes, capped out (~$25)", 25.0)]:
        print(f"    {label:32}{money(creators * per):>9}/mo cost"
              f"  margin {(1 - per / PRICE['creator']) * 100:>+5.0f}%")


if __name__ == "__main__":
    main()

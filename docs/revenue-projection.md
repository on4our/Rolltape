# Revenue projection

What [the pricing plan](pricing.md) produces if it sells. Prices, caps and cost
constants are taken from that doc; the adoption numbers are assumptions and are marked
as such throughout. Nothing here is a forecast — it's the arithmetic of that pricing
under three adoption stories, so the shape of the business is visible before any of it
is built.

## Headline

Base case, three years out: **~$95k ARR**, 521 subscribers, ~$6.6k/month operating
profit.

| Base case | Subs | MRR | ARR | Revenue that year | Profit that year |
| --------- | ---- | --- | --- | ----------------- | ---------------- |
| End of year 1 | 140 | $1,478 | $17.7k | $8,135 | $7,417 |
| End of year 2 | 376 | $5,459 | $65.5k | $45,538 | $37,257 |
| End of year 3 | 521 | $7,898 | $94.8k | $82,539 | $68,865 |

Bracketed by the other two scenarios:

| End of year 3 | Subs | MRR | ARR | Year 3 profit |
| ------------- | ---- | --- | --- | ------------- |
| Conservative | 177 | $2,665 | $32.0k | $22,064 |
| Base | 521 | $7,898 | $94.8k | $68,865 |
| Optimistic | 1,298 | $19,703 | $236.4k | $174,582 |

Profit is operating margin only. It does not carry the founder's time, and it does not
carry the build cost of the licensed feed and the multi-tenancy work that Phase 3
depends on.

## The shape is front-loaded on margin, not revenue

This is the part worth internalising, and it falls out of the pricing doc's own
sequencing rather than from any assumption made here.

Months 1-12 sell the local app. There is no data licence to pay, because nothing is
being redistributed — the user's own machine fetches their own data. There is no
hosting bill, because there are no hosted renders. Fixed costs in year one are about
$25/month of ops. **Year 1 is roughly 91% margin on $8k of revenue**, which is a
strange and pleasant place to be, and it means the local-only launch funds itself from
the first handful of subscribers.

The cost base arrives all at once in month 13, when hosted unlocks: $100/month for the
feed, ~$40/month of hosting, plus per-subscriber egress. That still clears break-even
immediately at every scenario — the first hosted month is profitable in all three —
but it changes the business from "no costs" to "real costs," and every margin question
in this doc lives on the far side of that line.

The practical read: there is no scenario where hosted needs to be rushed. It is an
upgrade to a base that is already paying for itself.

## Phasing

Modelled in three phases, straight from the pricing doc's sequencing section.

| | Months | Sellable | Why |
| --- | --- | --- | --- |
| P1 | 1-6 | Hobbyist, local | "the launch is $9/month with the local app" |
| P2 | 7-12 | + Creator, local | brand kits and custom colours ship (roadmap #4) |
| P3 | 13-36 | + Studio, hosted | licensed feed and multi-tenancy land |

Tier mix follows what's actually sellable: 100% Hobbyist in P1, 70/30 in P2, and
55/35/10 across Hobbyist/Creator/Studio in P3. Blended ARPU in P3 is **$15.68/month**
before the residual Hobbyist-heavy cohorts from the early phases drag it to $15.16.

## Assumptions

Everything in this section is invented. It is the only part of the model that is.

| | Conservative | Base | Optimistic |
| --- | --- | --- | --- |
| New subs/month, P1 | 4 | 10 | 22 |
| New subs/month, P2 | 7 | 18 | 45 |
| New subs/month, P3 | 10 | 30 | 75 |

Plus, held constant across all three: **4%/month churn** net of the pause-don't-cancel
recovery, **30% of new subs choosing annual**, and the P3 tier mix above. A 4% monthly
churn implies a 25-month average subscription life, which is on the optimistic side of
normal for prosumer tooling and is defensible here mainly because the commitments
section of the pricing doc is explicitly built to suppress churn.

Scale check: base case reaches 521 subscribers by month 36. For a tool aimed at
finance and investing content creators, that is a low-single-digit-percent capture of a
plausible addressable market, sold by one person. It is not a heroic number, and the
conservative case at 177 is closer to what happens with no marketing at all.

## Unit economics

**Payment processing is not negligible on a $9 tier.** Stripe's 2.9% + $0.30 is 6.2% of
a $9 monthly charge, against 4.5% at $19 and 3.5% at $49. The fixed 30 cents is doing
most of that damage.

| | Price | Net of Stripe | LTV at 4% churn |
| --- | --- | --- | --- |
| Hobbyist | $9 | $8.44 | $211 |
| Creator | $19 | $18.15 | $454 |
| Studio | $49 | $47.28 | $1,182 |

Annual billing helps on fees — one transaction instead of twelve — but the 10x-monthly
price gives up 16.7%, so annual is net-negative on revenue per subscriber-year by about
14%. It buys cash upfront and lower churn instead. At the modelled 30% annual share the
whole effect is worth ±5% of MRR, which is small enough that the annual discount should
be judged on churn and cash flow, not on this line.

### One correction to the pricing doc

The pricing doc puts break-even against a $100/month feed at "about 11 hobbyists or 6
creators." On gross revenue that's right — 11.1 and 5.3 respectively. Net of payment
processing it's **11.8 hobbyists and 5.5 creators**, so the honest hobbyist number is
12, not 11. A one-subscriber correction on a threshold that gets crossed in the first
month or two, but the doc states it precisely enough to be worth stating precisely.

## Where this breaks

Three risks, in the order they'd actually bite.

**Churn is the dominant lever, by a wide margin.** Nothing else in the model moves the
year-3 number as much:

| Base case, month 36 MRR | | |
| --- | --- | --- |
| Baseline | $7,898 | |
| Churn 2%/mo | $10,021 | +27% |
| Churn 6%/mo | $6,372 | −19% |
| Churn 8%/mo | $5,255 | −33% |
| Mix skews to Hobbyist (75/20/5) | $6,340 | −20% |
| Mix skews to Creator (35/50/15) | $9,455 | +20% |
| Annual share 0% | $8,313 | +5% |
| Annual share 60% | $7,482 | −5% |

Churn swings the outcome ±33% where tier mix swings it ±20% and annual share ±5%. The
commitments in the pricing doc — pause rather than cancel, price locked while
subscribed, the perpetual fallback at twelve months — are therefore not goodwill
gestures. They are the highest-leverage revenue mechanism in the entire plan, and they
should be treated as load-bearing product features.

**The ProRes tail is a real margin hole, and the guard should ship with hosted rather
than wait.** The pricing doc prices a Creator maxing 250 renders entirely on ProRes at
~$25/month against $19 of revenue, and holds a sub-cap "in reserve" until someone
actually does it. At base-case scale that reserve is uncomfortable:

| Base case, 175 Creators at month 36 | Cost/mo | vs $3,324 revenue |
| --- | --- | --- |
| Realistic MP4-heavy mix (~$4/sub) | $700 | +79% margin |
| All-ProRes, capped out (~$25/sub) | $4,374 | −32% margin |

It does not take all 175 Creators behaving that way to hurt — it takes the top decile,
and transparent ProRes is exactly the feature a serious channel uses on every render.
The seven-day retention already bounds storage; the sub-cap is what bounds egress, and
it's cheaper to launch with it than to add it to people who have already learned to
render without one.

**The caps oversell a single render worker.** This one is structural rather than
financial, and it's the finding that most changes the Phase 3 build.

`RENDER_LOCK` serialises matplotlib and the Dockerfile pins `--workers 1`, so hosted
renders run one at a time. At the pricing doc's own figure of 30-40s for a 7.5s 1080p60
`final`, one worker does ~103 renders/hour — about 74,000/month flat out, or ~22,000 at
the 30% utilisation that keeps queue latency tolerable.

| Base case month 36, 521 subs | Renders/mo | Workers needed |
| --- | --- | --- |
| Quarter of cap used (realistic) | 27,884 | 1.3 |
| Half of cap used | 55,768 | 2.5 |
| Every sub maxes their cap | 111,535 | 5.0 |

Sold capacity if every seat maxed is 151% of a single worker running flat out, 24/7.
Even the realistic quarter-of-cap case needs more than one. So multi-tenancy is not
only the correctness problem the pricing doc describes — a shared job store behind the
`jobs.py` seam and renders in separate processes — it is a capacity requirement that
arrives at the same time paying customers do. Priority queue is sold as a Creator
feature; this is the arithmetic that says it's a genuinely scarce resource rather than
an artificial one.

## What would change the picture

The model holds tier prices fixed, because the pricing doc locks price while
subscribed, so ARPU expansion has to come from mix rather than from increases. That
makes two things disproportionately valuable, and both are cheap relative to what they
move:

- **Anything that moves Hobbyists to Creator.** The mix sensitivity is ±20% and the
  gap is $10/month per subscriber. Custom theme colours is the lead item here and the
  pricing doc already notes it's close to free to build, since a user-defined entry
  alongside `THEMES` needs no other change.
- **Anything that reduces churn.** ±33%, and the levers are already written down in
  the commitments section rather than needing to be invented.

Studio is 10% of subscribers and 32% of blended ARPU, which makes the batch render and
API line worth more than its position on the roadmap suggests — but it is also the tier
most exposed to the capacity finding above, so it should not be sold ahead of the
multi-worker build.

## Reproducing this

```bash
python3 scripts/revenue_model.py
```

Standard library only. Every price, cap and cost constant in it is lifted from
`docs/pricing.md` and should only change when that doc does; the adoption inputs
(`SCENARIOS`, `CHURN`, `ANNUAL_SHARE`, `MIX`) sit in their own block so they can be
argued with without touching the arithmetic. Every number in this doc comes out of that
script — change an assumption, re-run it, and the tables above are what should be
updated.

Remaining constants: Stripe 2.9% + $0.30, data licence $100/month (top of the doc's
$30-100 range), hosting $40/month once hosted exists, ops $25/month throughout, and
average hosted egress of $0.50/$2.00/$6.00 per Hobbyist/Creator/Studio per month
against the doc's ~$4 realistic and ~$25 maxed Creator figures.

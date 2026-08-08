# Pricing

The plan for charging for Rolltape. Decided, not shipped — nothing in the code enforces
any of this yet. See [Sequencing](#sequencing) for what has to exist first.

This supersedes the "watermarking on a free tier, render credits" sketch in CLAUDE.md's
roadmap. Both of those ideas were dropped, for reasons in [What we're not
doing](#what-were-not-doing).

## The tiers

|                            | **Hobbyist $9** | **Creator $19** | **Studio $49** |
| -------------------------- | --------------- | --------------- | -------------- |
| Local app                  | Unlimited       | Unlimited       | Unlimited      |
| Hosted renders             | 70/mo           | 250/mo          | 1,000/mo       |
| Resolution                 | 1080p           | 1440p           | 1440p          |
| Quality tiers              | draft, final    | + max           | + max          |
| Transparent ProRes         | —               | Yes             | Yes            |
| Footer                     | Yes             | Yes             | Yes            |
| Brand kits, custom colours | —               | Yes             | Yes            |
| Queue                      | Standard        | Priority        | Priority       |
| Seats                      | 1               | 1               | 3              |
| Batch render, API          | —               | —               | Yes            |

Annual is 10x monthly — $90, $190, $490. Two months free, and one rule instead of three
discount percentages to remember.

Subscription rather than a one-time price because the obligation is ongoing. Rolltape
depends on a data source that breaks on someone else's schedule — yfinance breaks whenever
Yahoo changes an endpoint, which is why the Stooq fallback exists at all. A perpetual
licence sells perpetual upstream-chasing with nothing funding it.

## What separates the tiers

**Gate on what costs money. Never on what doesn't.**

That rule is what keeps the ladder honest, and every Creator gate has a real cost behind
it:

- **1440p and the `max` tier** run preset `slow` — the ~70s render CLAUDE.md warns gets
  OOM-killed on small hosts.
- **Transparent ProRes** produces roughly 1.1 GB for a 7.5s 1440p60 clip. Egress on that
  dwarfs the encode; see [Cost model](#cost-model).
- **Priority queue** is genuinely scarce. `RENDER_LOCK` serialises matplotlib, so the host
  renders one clip at a time and queue position is the resource being sold.
- **Seats and batch** are straightforwardly more usage.

Two things are never gated, on any tier:

- **Watermark-free output at full quality.** A hobbyist gets unwatermarked 1080p MP4,
  which is a complete publishable product. They are on the tier that costs little to
  serve, not a crippled one.
- **The footer field.** It already ships — `templates/index.html:353`, placeholder
  `@yourchannel`. Anything already in someone's hands stays in it.

## Render caps

**The caps count hosted renders only. Local renders are unlimited on every tier.**

Not a concession — an enforceability fact. A local render is a matplotlib call in a
process on the user's machine with no phone-home. Counting those means adding usage
reporting to the local app, which is bypassable in an afternoon and reads as surveillance
for the trouble. The line that does hold is the one at our own infrastructure: your
machine, your renders; the cap is on ours.

A useful side effect is that nobody is ever blocked from finishing a video. Hitting the
cap degrades convenience, not capability, which is what keeps a cap from becoming the
thing people resent.

**Previews don't count.** `/api/preview` returns a single still, not an encode. Only
finished exports draw down the allowance, so iterating on a chart is free.

At 3-8 renders per finished video, 70/mo covers roughly 9-20 videos — generous for someone
posting weekly. 250 covers a creator posting three times a week with room to iterate.
Studio's 1,000 is three seats at the Creator allowance plus headroom.

## Branding

Split by what already exists, because retro-gating a shipped capability is the one move
this whole structure is designed to avoid.

**Everyone keeps the footer.** Unchanged from how it works today.

**Creator adds** saved brand kits (theme, footer and title format as a named preset —
roadmap #4), **custom theme colours**, and a logo mark. Custom colours is the one to lead
with: CLAUDE.md notes that adding a theme requires no other change, so a user-defined entry
alongside `THEMES` is close to free to build and is exactly what a channel with a visual
identity wants.

**The data-source attribution is never removable, on any tier.** `renderers.py:185`
appends it to whatever the user typed rather than letting it be replaced, and that stays
true through brand kits. It's a factual disclosure about which source answered, not
decoration — and once a licensed feed replaces the current sources, attribution is
typically a contract term rather than a courtesy. Brand kits compose with that line; they
never suppress it.

## What we're not doing

**Render credits.** They meter something that isn't a cost. An h264 render is a fraction
of a cent of compute, and on the Hobbyist tier it runs on the customer's own CPU. Credits
would add billing surface, dashboards and refund cases to ration a non-resource, and would
throttle people exactly when they're most engaged. The real costs here are fixed — the
data licence is the same $30-100/month whether anyone renders or not — and fixed costs
want a flat subscription.

**Watermarked free output.** A watermarked chart can't go in the video, which is the
entire job. That makes it a demo that produces nothing usable rather than a taste of the
product, and it's the exact shape of the bait-and-switch this structure exists to avoid.

**A free tier.** Not out of stinginess: a free tier that later grows a paywall is the
failure mode we're designing against, and the cheapest insurance is to never create one.
The 14-day trial below does the same job of letting people evaluate, without presenting
itself as permanent.

## Commitments

These exist so that "we won't switch things up on you" is structural rather than a
promise.

- **Perpetual fallback.** Twelve months subscribed earns that version of the local app
  forever, including after cancelling. Hosted, updates and support stop; the software you
  paid for does not. Twelve months rather than immediate, or it's a one-time price wearing
  a subscription costume.
- **Pause, don't cancel.** Creators work in bursts, and the quiet month is where churn
  actually happens. A pause turns a permanent loss into a two-month gap.
- **Price locked while subscribed.** Your price doesn't rise as long as you stay.
- **14-day trial**, full Creator features, no card. A trial, not a tier — nothing is taken
  away at the end because nothing was presented as permanent.

## Cost model

Marginal cost per hosted render is small but not uniform, and the spread is what the tier
boundaries follow.

An h264 MP4 is a rounding error: ~30-40s of one core for a 7.5s 1080p60 `final`, which is
well under a cent, with egress on the finished file the larger half of it.

ProRes 4444 is a different animal. At 1440p60 it runs on the order of 1.17 Gbps, so a 7.5s
transparent clip lands near **1.1 GB** — roughly $0.10 to deliver at typical egress rates.
A Creator maxing 250 renders entirely on ProRes would cost ~$25/month against $19 of
revenue. A realistic mix, where most renders are MP4, lands nearer $4.

Two guards:

- **Seven-day retention on hosted output.** Downloaded and gone. Bounds storage outright,
  and egress is per-download regardless.
- **A sub-cap on transparent and 1440p renders, held in reserve.** Not worth the
  complexity until someone actually maxes the expensive path — but this is the number to
  watch first if hosted margins look wrong.

Break-even against a $100/month licensed feed is about 11 hobbyists or 6 creators. That
counts gross revenue — net of payment processing it's 12 hobbyists, since Stripe's fixed
30c is 6.2% of a $9 charge. See [Revenue projection](revenue-projection.md) for what
this pricing produces at scale, and for the two findings that bear on decisions left
open above: the ProRes sub-cap held in reserve, and the render caps against
single-worker throughput.

## Sequencing

Only the Hobbyist tier is shippable against today's codebase, and only its local half.
Everything hosted — on all three tiers — is blocked on two things:

1. **A licensed data feed.** The current sources can't be redistributed to paying users.
   Yahoo is scraped and the README already notes Stooq is a personal-use fallback that
   changes nothing about the licensing position, so neither one survives contact with a
   paying customer.
2. **Multi-tenancy.** `jobs.py` raises at import on any backend but memory, and the
   Dockerfile's `--workers 1` is load-bearing because job state lives in one process's
   memory. Hosted needs a shared job store behind the `jobs.py` seam. Renders already run
   in separate processes.

So the launch is $9/month with the local app, and hosted arrives later as an upgrade
included in tiers people already hold — not as a new paywall over something that used to
be free.

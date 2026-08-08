# Acquisition

How Rolltape gets users. Companion to [pricing.md](pricing.md), which settles what people
pay; this settles how they arrive. Same status: decided, not shipped.

## The problem isn't which channel

There is no path from stranger to paying customer. Not a narrow one — none.

- **Nowhere to send anyone.** `templates/` contains exactly one file, and it's the app.
  No landing page, no email capture, no waitlist.
- **Nothing to try.** The launch product is a local Python app: `pip install -r
  requirements.txt`, plus ffmpeg on PATH. That is the first thing an interested stranger
  meets.
- **No way to pay.** Nothing in the code enforces a tier, a trial or a subscription.

Picking marketing channels before those exist is optimising the top of a funnel that ends
in a wall. The order below is deliberate: fix the funnel shape, then pour people into it.

## What we're up against

This is the part that changes decisions, so it goes near the top.

The category is real and contested, and every serious competitor is **browser-based with a
free tier**:

| | Shape | Price |
|---|---|---|
| [ChartAnimator](https://www.chartanimator.io) | Browser, free tier | Pro / Elite, undisclosed |
| [KPI Studio](https://kpistudio.app) | Browser, exports ProRes 4444 + WebM alpha | — |
| [AlienArt](https://alienart.io) | Browser | $19/mo, $180/yr |
| [Viral Data Race](https://viraldatarace.com) | Browser, free 720p watermarked | Pro for 1080p |
| [Framechart](https://framechart.com) | Browser, RGBA PNG frames | — |
| [Flourish](https://flourish.studio) | Browser, no native video export | ~$79/mo |

Two findings worth recording because they contradict things we assumed:

**Transparent ProRes is not a differentiator.** KPI Studio exports ProRes 4444 with alpha,
everviz documents the same workflow, Framechart ships RGBA frame sequences. pricing.md
gates transparent output at Creator on cost grounds, which still holds — but it should not
be sold as something only we do, because it isn't.

**ChartAnimator occupies the obvious position.** It is aimed squarely at trading
educators — bar-by-bar candlestick animation, ICT order blocks, Smart Money Concept
visuals, fair value gaps, liquidity sweeps — and is pushing hard on syndicated press
releases. Its own materials describe a free plan with no card, and Pro and Elite tiers
above it.

Caveat on both: this is drawn from the vendors' own marketing and press releases, and
chartanimator.io is unreachable from this network, so the pricing and feature claims are
unverified. **Confirm them by hand before anything here gets built on top of them.**

## Where that leaves us

Don't fight ChartAnimator on candlesticks. It has the position, the free tier and the
distribution, and technical-analysis markup — order blocks, liquidity sweeps — is a
feature surface we haven't built and shouldn't start.

The gap it leaves is the one Rolltape already sits in. Look at what's in `CHARTS`
(`renderers.py:803`): comparison indexed to 100, bar comparison of total return and max
drawdown and annualised volatility, bar race, annotated timeline. Not one of those is a
trade-setup tool. They are **long-horizon investing** charts — "the Mag 7 over ten years",
"what $10k in each of these would be worth", "a timeline of the 2020 drawdown". That is a
different channel, a different audience and a different search term from day-trading
education.

So the positioning is: **animated charts for investing content, from a ticker.**

Two things hold that up:

- **Ticker in, not data in.** Almost every competitor takes pasted or uploaded rows. You
  type `AAPL` and `data.fetch()` does the rest. For someone covering markets weekly, that
  is the whole difference between a tool they use and a tool they mean to use.
- **The thumbnail comes out of the same render.** **Save this frame** writes the previewed
  frame as a full-resolution PNG — same chart, same moment, same size. Nobody else in the
  table above mentions solving the thumbnail. For a YouTuber the thumbnail is not a side
  quest, and shipping it in the same pass as the video is worth more than it sounds.

## The install is the bottleneck

Against six browser-based competitors, `pip install` plus "get ffmpeg on your PATH" is
where nearly everyone who was interested stops. The audience is investing YouTubers, not
Python developers. No amount of channel work survives that step.

This is the highest-leverage thing on the list, and it isn't marketing. Three fixes, in
order of what they cost:

**1. A public demo instance. Do this first.**

`ROLLTAPE_DEMO=1` already exists (`config.py`), the `Dockerfile` already runs the app under
gunicorn, and `data.fetch()` returns synthetic series for any ticker typed. Every control
works. The footer stamps `Demo data` (`data.SOURCE_LABELS`) so nothing generated can
quietly end up in someone's video.

Which means a hosted instance running in demo mode carries **no data-licensing exposure at
all** — the licensing block in pricing.md is about redistributing *market* data to paying
users, and there is no market data in demo mode. It is the one piece of hosted we can put
in public today, and it converts "read about it" into "used it" with no install.

Cost is one small container. Keep `--workers 1` and expect it to be slow under load;
that's acceptable for a demo and honest about what the local app is for.

**2. A packaged build.** PyInstaller with ffmpeg bundled, one download per platform. This
is the real fix for the paid local tier and it is the difference between a $9 product an
investing YouTuber can buy and one they can't. Nothing about the app resists this —
single process, no build step, no database.

**3. Hosted rendering.** Blocked on a licensed feed and multi-tenancy, per pricing.md's
sequencing. Unchanged. Not on the acquisition path for launch.

## Channels, in order

Ranked for where this actually is, not for where it might be at scale.

1. **The channel itself.** Every video already ships a demo of the product in it. Make
   that legible: one evergreen "how I make every chart in these videos" video, pinned,
   with a link. It costs one afternoon, targets exactly the people who want it, and it is
   the only channel here with zero acquisition cost.
2. **The demo instance as the destination.** Every link from everywhere below points at
   something people can use in ten seconds, not at install instructions.
3. **Show HN.** Local-first, no build step, no database, one file of HTML — this is a
   posture HN rewards, and the codebase honestly has it. One shot, spiky, worth taking
   once the demo is live and not before.
4. **r/dataisbeautiful and adjacent.** Bar races have a long history of travelling there.
   Post the output, not the tool. Read each subreddit's self-promotion rules first;
   r/investing and r/algotrading enforce theirs strictly.
5. **Direct outreach.** Thirty to fifty mid-size investing channels, a free year each in
   exchange for actually using it and telling you where it breaks. This is the highest
   conversion per hour of anything on the list and the only one that also returns product
   feedback.
6. **Product Hunt.** After the packaged build exists, not before — the launch spike is
   wasted on an audience that has to `pip install`.
7. **SEO.** "Bar chart race stocks", "animated stock comparison chart". Real long-tail
   intent, slow to compound, worth starting only once there's a page to rank.

## What we're not doing

**Paid ads.** Customer acquisition cost against $9/month doesn't clear, and there is no
retention data yet to argue otherwise.

**A watermarked free tier.** pricing.md settled this and the reasoning stands. Worth
naming what it costs, though: it's the category norm here, and refusing it means every
user has to come from owned or earned media. The demo instance does the evaluation job
that a free tier would, without shipping output nobody can use.

**An affiliate or referral programme.** Both need a product people already pay for. Nothing
to refer yet.

**Chasing ChartAnimator's press-release strategy.** Syndicated releases on aggregator sites
buy indexation, not customers, and a solo channel's own audience is a better asset than a
wire service.

## The first twenty users

Get them by hand. Install it for people on a call, watch where they get stuck, fix that.
Twenty users who render weekly tell you more than a thousand signups, and at this stage a
signup number would mostly be measuring the demo instance.

Manual is fine for payment too — a Stripe payment link and an emailed download beats
building billing for a customer count you can hold in your head.

## Sequencing

1. Verify the competitor claims above by hand. They're from vendor marketing.
2. Deploy the demo instance. Uses code that exists today.
3. Landing page with email capture — what it is, the demo link, three example clips.
4. Package the local app with ffmpeg bundled.
5. Stripe payment link, manual fulfilment, first paying users.
6. The "how I make these charts" video, pointing at all of it.
7. Show HN, then outreach, then Product Hunt.

Steps 1-3 are days. Nothing below step 4 matters until step 4 exists.

## What to watch

Only two numbers mean anything early: **how many demo visitors render something**, and
**how many trial users render in week two**. The first says the product explains itself,
the second says it's worth paying for. Signups, stars and traffic don't distinguish
between a good week and a good product.

## Open question

The whole ranking above puts the founder's own channel first, and its subscriber count
isn't recorded anywhere in this repo. At a few hundred subscribers step 1 is direct
outreach instead. At tens of thousands it dominates everything else on the list and the
rest is a rounding error. **This ordering assumes the latter — correct it if that's
wrong.**

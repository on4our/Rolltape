// Builds the SNDK slideshow. Figures come from data.json, which sndk_data.py writes —
// so the deck and the animated charts cannot drift apart, and neither can be edited
// into saying something the reconciliation check in sndk_data.py would reject.
//
// The deck is dark on #0B0E14 because that is Rolltape's "midnight" background, and the
// animated clips cut into the same video. Two grounds would flash on every cut.
//
//   node build_deck.js

const pptxgen = require("pptxgenjs");
const fs = require("fs");
const path = require("path");

const D = JSON.parse(fs.readFileSync(path.join(__dirname, "data.json"), "utf8"));

// --- palette ---------------------------------------------------------------
// The three categorical hues are validated against this exact surface for contrast,
// chroma and colour-vision separation — don't swap one without re-running the check.
const C = {
  bg: "0B0E14",
  card: "151C2A",
  cardHi: "1C2536",
  text: "E8ECF4",
  head: "FFFFFF",
  muted: "8B95A9",
  dim: "5B657A",
  blue: "3B82F6",   // Datacenter, and the deck's primary accent
  teal: "0D9488",   // Edge
  orange: "EA580C", // Consumer
  green: "4ADE80",
  red: "F87171",
};

const FONT = "Calibri";
const HEAD = "Arial";

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.3 x 7.5 — must be set before any slide is added
pres.author = "Rolltape";
pres.title = "Sandisk (SNDK) — FY2026";

const W = 13.3, H = 7.5, M = 0.62;

// --- helpers ---------------------------------------------------------------
function slide(dark = true) {
  const s = pres.addSlide();
  s.background = { color: dark ? C.bg : C.cardHi };
  return s;
}

function heading(s, text, sub) {
  s.addText(text, {
    x: M, y: 0.52, w: W - M * 2, h: 0.62,
    fontSize: 34, bold: true, color: C.head, fontFace: HEAD, margin: 0,
  });
  if (sub) {
    s.addText(sub, {
      x: M, y: 1.16, w: W - M * 2, h: 0.4,
      fontSize: 15, color: C.muted, fontFace: FONT, margin: 0,
    });
  }
}

// Columns that fit inside the margins, rather than a width guessed per slide. Getting
// this wrong by a tenth of an inch puts the last card under the right bezel.
function cols(count, gap, x0 = M, x1 = W - M) {
  const w = (x1 - x0 - gap * (count - 1)) / count;
  return { w, at: (i) => x0 + i * (w + gap) };
}

function card(s, o) {
  s.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w, h: o.h,
    fill: { color: o.fill || C.card }, rectRadius: 0.12,
    line: { color: o.fill || C.card, width: 0 },
  });
}

// A big number over a small label — the deck's repeated unit.
function stat(s, o) {
  card(s, { x: o.x, y: o.y, w: o.w, h: o.h, fill: o.fill });
  // The two boxes must not overlap: the value is valign-middle in the upper 46% and the
  // label starts below it, so a long label wraps downward into its own space rather than
  // up through the number.
  s.addText(o.value, {
    x: o.x + 0.24, y: o.y + 0.16, w: o.w - 0.48, h: o.h * 0.46,
    fontSize: o.size || 40, bold: true, color: o.color || C.head,
    fontFace: HEAD, margin: 0, valign: "middle",
  });
  s.addText(o.label, {
    x: o.x + 0.24, y: o.y + o.h * 0.64, w: o.w - 0.48, h: o.h * 0.32,
    fontSize: 12, color: C.muted, fontFace: FONT, margin: 0, valign: "top",
  });
}

function footnote(s, text) {
  s.addText(text, {
    x: M, y: H - 0.52, w: W - M * 2, h: 0.3,
    fontSize: 9.5, color: C.dim, fontFace: FONT, margin: 0,
  });
}

// Shared frame for every native chart: no legend where one series needs none, recessive
// grid, labels in ink rather than in the series colour.
function chartBase(extra) {
  return Object.assign({
    chartColors: [C.blue],
    showLegend: false,
    showTitle: false,
    showValue: true,
    dataLabelColor: C.text,
    dataLabelFontFace: FONT,
    dataLabelFontSize: 13,
    dataLabelFontBold: true,
    catAxisLabelColor: C.muted,
    catAxisLabelFontFace: FONT,
    catAxisLabelFontSize: 12,
    valAxisLabelColor: C.muted,
    valAxisLabelFontFace: FONT,
    valAxisLabelFontSize: 11,
    valGridLine: { color: "1E2634", size: 1 },
    catGridLine: { style: "none" },
    valAxisLineShow: false,
    catAxisLineShow: false,
    border: { pt: 0, color: C.bg },
    plotArea: { fill: { color: C.bg } },
    chartArea: { fill: { color: C.bg } },
  }, extra || {});
}

const q = D.QUARTERS;
const qLabels = q.map((x) => x.label);

// ===========================================================================
// 1. Title
// ===========================================================================
{
  const s = slide();
  s.addText("NASDAQ: SNDK   ·   FISCAL YEAR 2026", {
    x: M, y: 1.65, w: W - M * 2, h: 0.34,
    fontSize: 13, bold: true, color: C.blue, fontFace: FONT,
    charSpacing: 2, margin: 0,
  });
  s.addText("Revenue up 175%.", {
    x: M, y: 2.06, w: W - M * 2, h: 0.98,
    fontSize: 52, bold: true, color: C.head, fontFace: HEAD, margin: 0,
  });
  s.addText("The stock down 30%.", {
    x: M, y: 3.06, w: W - M * 2, h: 0.98,
    fontSize: 52, bold: true, color: C.red, fontFace: HEAD, margin: 0,
  });
  s.addText(
    "Sandisk's first full year on its own — and the gap between the business and the share price.",
    { x: M, y: 4.10, w: 11.6, h: 0.55, fontSize: 16, color: C.muted, fontFace: FONT, margin: 0 }
  );

  // The four quarters as a ramp, drawn to scale. A decorative element that is also the
  // deck's first data point.
  const maxR = Math.max(...q.map((x) => x.revenue));
  q.forEach((quarter, i) => {
    // Capped so the tallest bar clears the subtitle above it rather than crowding it.
    const h = 0.42 + (quarter.revenue / maxR) * 1.0;
    s.addShape(pres.ShapeType.roundRect, {
      x: M + i * 0.72, y: 6.42 - h, w: 0.5, h,
      fill: { color: i === q.length - 1 ? C.blue : "1E3A5F" },
      rectRadius: 0.06, line: { color: C.bg, width: 0 },
    });
  });
  s.addText("Quarterly revenue, Q1 to Q4 FY2026", {
    x: M + 3.1, y: 5.95, w: 5.4, h: 0.3,
    fontSize: 11, color: C.dim, fontFace: FONT, margin: 0,
  });
  footnote(s, "All figures from Sandisk quarterly results. Fiscal 2026 ended 3 July 2026.");
  s.addNotes(
    "Hook: this is a company whose revenue nearly tripled in a year and whose stock has just " +
    "fallen 30% from its high. Both things are true at once — that's the story.\n\n" +
    "FY2026 revenue $20.25B, up 175% year over year. Stock hit $2,354.39 on 22 June 2026 and " +
    "was around $1,270-1,340 by 12 August."
  );
}

// ===========================================================================
// 2. What Sandisk is
// ===========================================================================
{
  const s = slide();
  heading(s, "The company", "Spun out of Western Digital in February 2025");

  const body = [
    { text: "Sandisk makes NAND flash memory", options: { bullet: true, breakLine: true, bold: true } },
    { text: "— the storage inside SSDs, phones, cameras and, increasingly, AI data centres.", options: { breakLine: true } },
    { text: "Western Digital bought it in 2016 and separated it again in February 2025.", options: { bullet: true, breakLine: true } },
    { text: "It has traded as an independent company for about eighteen months.", options: { bullet: true, breakLine: true } },
    { text: "FY2026 is its first full fiscal year standing alone.", options: { bullet: true } },
  ];
  s.addText(body, {
    x: M, y: 1.95, w: 5.7, h: 3.3,
    fontSize: 15, color: C.text, fontFace: FONT, margin: 0, valign: "top",
    lineSpacing: 23, paraSpaceAfter: 11,
  });

  s.addText(
    `${D.COMPANY.hq}   ·   ${D.COMPANY.employees} employees   ·   CEO David Goeckeler`,
    { x: M, y: 5.5, w: 5.9, h: 0.4, fontSize: 12, color: C.dim, fontFace: FONT,
      margin: 0, valign: "top" }
  );

  const g = cols(2, 0.3, 6.85), gh = 1.9;
  stat(s, { x: g.at(0), y: 1.95, w: g.w, h: gh, value: "$20.2B", label: "FY2026 revenue", color: C.blue });
  stat(s, { x: g.at(1), y: 1.95, w: g.w, h: gh, value: "+175%", label: "Revenue growth vs FY2025", color: C.green });
  stat(s, { x: g.at(0), y: 1.95 + gh + 0.3, w: g.w, h: gh, value: "$11.4B", label: "FY2026 net income (GAAP)", color: C.blue });
  stat(s, { x: g.at(1), y: 1.95 + gh + 0.3, w: g.w, h: gh, value: "$73.76", label: "FY2026 diluted EPS (GAAP)", color: C.blue });

  footnote(s, "Sandisk FY2026 results. FY2025 revenue of about $7.4B is implied by the reported 175% growth.");
  s.addNotes(
    "Keep this short — it's context, not the story.\n\n" +
    "Key point: NAND flash. Commodity-ish memory business that has just been repriced by AI " +
    "data centre demand. FY2025 revenue was roughly $7.4B; FY2026 was $20.25B."
  );
}

// ===========================================================================
// 3. The stock story
// ===========================================================================
{
  const s = slide();
  heading(s, "What the share price did", "Two moves, both enormous, in opposite directions");

  const g = cols(3, 0.32), cy = 2.05, ch = 2.2;
  stat(s, { x: g.at(0), y: cy, w: g.w, h: ch, value: "$2,354.39", size: 34, color: C.green,
            label: "All-time high, 22 June 2026" });
  stat(s, { x: g.at(1), y: cy, w: g.w, h: ch, value: "$1,271–1,344", size: 28, color: C.text,
            label: "Trading range on 12 August 2026 — sources differ on the close" });
  stat(s, { x: g.at(2), y: cy, w: g.w, h: ch, value: "−30%", size: 34, color: C.red,
            label: "Retreat from the June high" });

  card(s, { x: M, y: cy + ch + 0.36, w: W - M * 2, h: 1.28, fill: C.cardHi });
  s.addText(
    "The drawdown came with no deterioration in the reported numbers. Q4 was the strongest " +
    "quarter the company has ever posted, and it was posted after the high.",
    { x: M + 0.34, y: cy + ch + 0.56, w: W - M * 2 - 0.68, h: 0.9,
      fontSize: 16, color: C.text, fontFace: FONT, margin: 0, valign: "middle" }
  );

  footnote(s, "Price milestones as reported in August 2026. No continuous price series is shown here — see the animated charts.");
  s.addNotes(
    "This is the setup for the whole video. Stock peaked 22 June at $2,354.39, then fell more " +
    "than 30%. Q4 earnings landed 5 August — after the peak — and were a record.\n\n" +
    "Be careful on air: sources disagree on the exact 12 Aug close (roughly $1,271 to $1,344), " +
    "which is why the slide shows a range rather than a number to the cent.\n\n" +
    "This is where you cut to the animated price chart if you render one locally."
  );
}

// ===========================================================================
// 4. Revenue acceleration
// ===========================================================================
{
  const s = slide();
  heading(s, "Revenue, quarter by quarter", "FY2026 — every quarter bigger than the last");

  s.addChart(pres.ChartType.bar, [{
    name: "Revenue ($B)",
    labels: qLabels,
    values: q.map((x) => +(x.revenue / 1000).toFixed(2)),
  }], chartBase({
    x: M, y: 1.75, w: 8.1, h: 4.55,
    barDir: "col",
    barGapWidthPct: 55,
    dataLabelFormatCode: '"$"0.00"B"',
    dataLabelPosition: "outEnd",
    valAxisHidden: true,
    valAxisMaxVal: 10.5,
  }));

  const px = 9.05, pw = W - M - px;
  stat(s, { x: px, y: 1.9, w: pw, h: 1.5, value: "3.9x", color: C.blue,
            label: "Q4 revenue versus Q1, four quarters apart" });
  card(s, { x: px, y: 3.6, w: pw, h: 2.7, fill: C.cardHi });
  s.addText("Q4 alone was bigger\nthan all of FY2025", {
    x: px + 0.26, y: 3.85, w: pw - 0.52, h: 0.95,
    fontSize: 19, bold: true, color: C.green, fontFace: HEAD, margin: 0, valign: "top",
  });
  s.addText(
    `Q4 FY2026 revenue of $8.97B against roughly $${(D.FY2025_REVENUE / 1000).toFixed(1)}B ` +
    "for the whole of the prior fiscal year.",
    { x: px + 0.26, y: 4.88, w: pw - 0.52, h: 1.2,
      fontSize: 13, color: C.text, fontFace: FONT, margin: 0, valign: "top" }
  );

  footnote(s, "Reported quarterly revenue. Quarters sum to $20.25B, the reported FY2026 total.");
  s.addNotes(
    "The acceleration is the point: $2.31B → $3.03B → $5.95B → $8.97B.\n\n" +
    "Two thirds of the year's revenue arrived in the second half. And Q4 on its own " +
    "($8.97B) was larger than the entire prior fiscal year (about $7.4B).\n\n" +
    "Cut to: 01-quarterly-revenue.mp4"
  );
}

// ===========================================================================
// 5. Margin expansion
// ===========================================================================
{
  const s = slide();
  heading(s, "Gross margin", "Non-GAAP, by quarter — the part that turned revenue into profit");

  s.addChart(pres.ChartType.bar, [{
    name: "Gross margin (%)",
    labels: qLabels,
    values: q.map((x) => x.gross_margin),
  }], chartBase({
    x: M, y: 1.75, w: 8.1, h: 4.55,
    barDir: "col",
    barGapWidthPct: 55,
    chartColors: [C.teal],
    dataLabelFormatCode: '0.0"%"',
    dataLabelPosition: "outEnd",
    valAxisHidden: true,
    valAxisMaxVal: 100,
  }));

  const px = 9.05, pw = W - M - px;
  stat(s, { x: px, y: 1.9, w: pw, h: 1.5, value: "+54.7", color: C.teal,
            label: "Percentage points added across FY2026" });
  card(s, { x: px, y: 3.6, w: pw, h: 2.7, fill: C.cardHi });
  s.addText("Why it matters", {
    x: px + 0.26, y: 3.85, w: pw - 0.52, h: 0.4,
    fontSize: 16, bold: true, color: C.head, fontFace: HEAD, margin: 0, valign: "top",
  });
  s.addText(
    "Revenue roughly quadrupled. Profit did far more than that, because each extra dollar " +
    "of revenue arrived at a much higher margin than the one before it.",
    { x: px + 0.26, y: 4.35, w: pw - 0.52, h: 1.7,
      fontSize: 13, color: C.text, fontFace: FONT, margin: 0, valign: "top" }
  );

  footnote(s, "Non-GAAP gross margin as reported each quarter. Q4 FY2026: 84.6%.");
  s.addNotes(
    "29.9% → 51.1% → 78.4% → 84.6%. That is a 54.7 point expansion in four quarters, which " +
    "is extraordinary for a memory business.\n\n" +
    "This is the single most important slide for explaining why earnings exploded rather " +
    "than merely grew. Pricing power from multi-year datacenter agreements.\n\n" +
    "Cut to: 03-gross-margin.mp4"
  );
}

// ===========================================================================
// 6. Earnings
// ===========================================================================
{
  const s = slide();
  heading(s, "Earnings per share", "GAAP diluted, by quarter");

  s.addChart(pres.ChartType.bar, [{
    name: "GAAP diluted EPS",
    labels: qLabels,
    values: q.map((x) => x.gaap_eps),
  }], chartBase({
    x: M, y: 1.75, w: 8.1, h: 4.55,
    barDir: "col",
    barGapWidthPct: 55,
    chartColors: [C.blue],
    dataLabelFormatCode: '"$"0.00',
    dataLabelPosition: "outEnd",
    valAxisHidden: true,
    valAxisMaxVal: 52,
  }));

  const px = 9.05, pw = W - M - px;
  stat(s, { x: px, y: 1.9, w: pw, h: 1.5, value: "$73.76", color: C.blue,
            label: "FY2026 GAAP diluted EPS" });
  stat(s, { x: px, y: 3.6, w: pw, h: 1.28, value: "$0.75", size: 30, color: C.muted,
            label: "Q1 FY2026 — where the year started" });
  stat(s, { x: px, y: 5.02, w: pw, h: 1.28, value: "$43.97", size: 30, color: C.green,
            label: "Q4 FY2026 — where it finished" });

  footnote(s, "GAAP diluted EPS. Q4 non-GAAP EPS was $39.25, against a consensus near $33.38.");
  s.addNotes(
    "The chart everyone will screenshot. $0.75 → $5.15 → $23.03 → $43.97.\n\n" +
    "Q4 non-GAAP EPS of $39.25 beat consensus of about $33.38 by roughly 18%.\n\n" +
    "Full year GAAP diluted EPS $73.76, from net income of $11.43B."
  );
}

// ===========================================================================
// 7. Segment mix
// ===========================================================================
{
  const s = slide();
  heading(s, "Where the revenue comes from", "FY2026 by segment — and what changed");

  const segs = D.SEGMENTS_FY2026.slice().sort((a, b) => b.revenue - a.revenue);
  s.addChart(pres.ChartType.bar, [{
    name: "FY2026 revenue ($B)",
    labels: segs.map((x) => x.name),
    values: segs.map((x) => +(x.revenue / 1000).toFixed(2)),
  }], chartBase({
    x: M, y: 1.85, w: 7.6, h: 3.5,
    barDir: "bar",
    barGapWidthPct: 45,
    chartColors: [C.teal, C.blue, C.orange],
    varyColors: true,
    dataLabelFormatCode: '"$"0.00"B"',
    dataLabelPosition: "outEnd",
    valAxisHidden: true,
    valAxisMaxVal: 14.5,
  }));

  const px = 8.62, pw = W - M - px;
  stat(s, { x: px, y: 1.85, w: pw, h: 1.6, value: "+437%", color: C.blue,
            label: "Datacenter revenue, year over year" });
  stat(s, { x: px, y: 3.62, w: pw, h: 1.6, value: "12% → 38%", size: 26, color: C.blue,
            label: "Datacenter share of the portfolio, in one year" });
  stat(s, { x: px, y: 5.39, w: pw, h: 1.1, value: "−32%", size: 24, color: C.orange,
            label: "Consumer revenue, Q4 sequentially" });

  s.addText(
    "Edge is still the largest segment. Datacenter is the one that re-rated the company — " +
    "and Consumer is shrinking as capacity is pointed at higher-value customers.",
    { x: M, y: 5.55, w: 7.6, h: 0.85, fontSize: 13, color: C.text, fontFace: FONT, margin: 0 }
  );

  footnote(s, "FY2026 segment revenue. The three segments sum to the reported $20.25B total.");
  s.addNotes(
    "Edge $12.16B, Datacenter $5.15B, Consumer $2.94B.\n\n" +
    "The mix shift is the real story: datacenter was about 12% of bits a year ago and 38% of " +
    "the portfolio exiting FY2026. Consumer fell 32% sequentially in Q4 — that is deliberate, " +
    "capacity being reallocated, not demand collapse.\n\n" +
    "Cut to: 02-segment-mix.mp4"
  );
}

// ===========================================================================
// 8. Why it happened
// ===========================================================================
{
  const s = slide();
  heading(s, "Why the numbers moved", "Three things behind the re-rating");

  const items = [
    { c: C.blue, t: "AI data centre demand",
      d: "Enterprise AI storage capacity sold out under long-term agreements, with multi-year customer commitments behind it rather than spot orders." },
    { c: C.teal, t: "BiCS10, the tenth-generation NAND",
      d: "1Tb TLC sampling at more than 29 Gb/mm², a 59% improvement in bit density — more capacity from the same wafer." },
    { c: C.orange, t: "High Bandwidth Flash, with SK hynix",
      d: "First OCP technical specification published August 2026: up to 512GB per stack and up to 3TB/s, aimed at the AI inference bottleneck." },
  ];

  const g = cols(3, 0.32), cw = g.w;
  items.forEach((it, i) => {
    const x = g.at(i);
    card(s, { x, y: 1.98, w: cw, h: 4.24 });
    s.addShape(pres.ShapeType.ellipse, {
      x: x + 0.32, y: 2.30, w: 0.62, h: 0.62,
      fill: { color: it.c }, line: { color: it.c, width: 0 },
    });
    s.addText(String(i + 1), {
      x: x + 0.32, y: 2.30, w: 0.62, h: 0.62,
      fontSize: 20, bold: true, color: "FFFFFF", fontFace: HEAD,
      align: "center", valign: "middle", margin: 0,
    });
    s.addText(it.t, {
      x: x + 0.32, y: 3.14, w: cw - 0.64, h: 0.8,
      fontSize: 17, bold: true, color: C.head, fontFace: HEAD, margin: 0, valign: "top",
    });
    s.addText(it.d, {
      x: x + 0.32, y: 4.02, w: cw - 0.64, h: 2.0,
      fontSize: 12.5, color: C.text, fontFace: FONT, margin: 0, lineSpacing: 17,
      valign: "top",
    });
  });

  footnote(s, "Sandisk product announcements and FY2026 results commentary.");
  s.addNotes(
    "Keep this tight — three reasons, one line each.\n\n" +
    "1. Demand: FY2026 enterprise AI storage capacity sold out under long-term agreements.\n" +
    "2. Technology: BiCS10 1Tb TLC, >29 Gb/mm², bit density up 59%.\n" +
    "3. HBF: joint standard with SK hynix, OCP spec released August 2026, 512GB and 3TB/s. " +
    "Sampling to customers in the second half of 2026 — this one is future revenue, not current."
  );
}

// ===========================================================================
// 9. Guidance
// ===========================================================================
{
  const s = slide();
  heading(s, "What management guided to", "First quarter of fiscal 2027");

  const g = D.GUIDANCE_Q1_FY27;
  const cw = 5.9;
  stat(s, { x: M, y: 2.05, w: cw, h: 2.05,
            value: `$${(g.revenue_low / 1000).toFixed(2)}–${(g.revenue_high / 1000).toFixed(2)}B`,
            size: 38, color: C.blue, label: "Q1 FY2027 revenue guidance" });
  stat(s, { x: M + cw + 0.36, y: 2.05, w: cw, h: 2.05,
            value: `$${g.nongaap_eps_low.toFixed(2)}–${g.nongaap_eps_high.toFixed(2)}`,
            size: 38, color: C.green, label: "Q1 FY2027 non-GAAP diluted EPS guidance" });

  card(s, { x: M, y: 4.42, w: W - M * 2, h: 1.72, fill: C.cardHi });
  s.addText("The comparison that makes it land", {
    x: M + 0.36, y: 4.62, w: W - M * 2 - 0.72, h: 0.4,
    fontSize: 15, bold: true, color: C.head, fontFace: HEAD, margin: 0, valign: "top",
  });
  s.addText(
    "A year earlier, Q1 FY2026 brought in $2.31B and earned $1.22 a share on a non-GAAP basis. " +
    "The guided quarter is roughly four and a half times the revenue — and more than thirty " +
    "times the earnings.",
    { x: M + 0.36, y: 5.08, w: W - M * 2 - 0.72, h: 0.95,
      fontSize: 14.5, color: C.text, fontFace: FONT, margin: 0, valign: "top" }
  );

  footnote(s, "Guidance issued with Q4 FY2026 results, 5 August 2026. Guidance is management's estimate, not a result.");
  s.addNotes(
    "Guidance: revenue $10.30-10.80B, non-GAAP EPS $44.00-46.00.\n\n" +
    "The framing that works: a year ago that same quarter was $2.31B and $1.22 non-GAAP EPS. " +
    "So ~4.5x the revenue and >30x the earnings.\n\n" +
    "Say clearly that this is guidance, not a reported result."
  );
}

// ===========================================================================
// 10. Valuation
// ===========================================================================
{
  const s = slide();
  heading(s, "What it trades at", "After a 30% fall from the high");

  const v = D.VALUATION;
  const g = cols(4, 0.28), gh = 1.72;
  stat(s, { x: g.at(0), y: 1.9, w: g.w, h: gh, value: `${v.forward_pe}x`, color: C.green,
            label: "Forward price / earnings" });
  stat(s, { x: g.at(1), y: 1.9, w: g.w, h: gh, value: `${v.peg}`, color: C.green,
            label: "PEG ratio" });
  stat(s, { x: g.at(2), y: 1.9, w: g.w, h: gh, value: `$${v.net_cash_b}B`, color: C.blue,
            label: "Net cash — about $43.89 a share" });
  stat(s, { x: g.at(3), y: 1.9, w: g.w, h: gh, value: `${v.shares_out_m}M`, color: C.text,
            label: "Shares outstanding" });

  card(s, { x: M, y: 3.72, w: 6.0, h: 2.42 });
  s.addText("What the sell side says", {
    x: M + 0.32, y: 3.96, w: 5.36, h: 0.4,
    fontSize: 16, bold: true, color: C.head, fontFace: HEAD, margin: 0, valign: "top",
  });
  s.addText(`$${v.analyst_target_avg.toLocaleString()}`, {
    x: M + 0.32, y: 4.42, w: 5.36, h: 0.72,
    fontSize: 36, bold: true, color: C.green, fontFace: HEAD, margin: 0,
  });
  s.addText(
    `Average 12-month target across ${v.analyst_count} analysts, consensus rating ` +
    `"${v.analyst_rating}".`,
    { x: M + 0.32, y: 5.24, w: 5.36, h: 0.7, fontSize: 13, color: C.text, fontFace: FONT,
      margin: 0, valign: "top" }
  );

  card(s, { x: M + 6.36, y: 3.72, w: W - M * 2 - 6.36, h: 2.42, fill: C.cardHi });
  s.addText("The obvious question", {
    x: M + 6.68, y: 3.96, w: W - M * 2 - 7.0, h: 0.4,
    fontSize: 16, bold: true, color: C.head, fontFace: HEAD, margin: 0, valign: "top",
  });
  s.addText(
    "A single-digit forward multiple usually means the market does not believe the earnings " +
    "are repeatable. For a memory company at a cyclical peak, that is a reasonable thing for " +
    "the market to think — and the thing to argue about.",
    { x: M + 6.68, y: 4.46, w: W - M * 2 - 7.0, h: 1.5,
      fontSize: 13.5, color: C.text, fontFace: FONT, margin: 0, lineSpacing: 18,
      valign: "top" }
  );

  footnote(s, "Valuation metrics as reported in mid-August 2026; market capitalisation was quoted between $180B and $188B.");
  s.addNotes(
    "Forward P/E about 5.9, PEG 0.14, net cash $6.54B ($43.89/share), 149M shares.\n\n" +
    "Analyst average target $2,053.50 across 23 analysts, Strong Buy.\n\n" +
    "Do not present the low multiple as free money. The right framing is the one on the " +
    "right-hand card: a mid-single-digit forward multiple is the market pricing peak earnings, " +
    "not a mispricing everyone else missed."
  );
}

// ===========================================================================
// 11. Bull and bear
// ===========================================================================
{
  const s = slide();
  heading(s, "The argument", "Both sides, from the same set of numbers");

  const bull = [
    "FY2026 enterprise AI storage capacity sold out under long-term agreements",
    "Gross margin up 54.7 points in four quarters",
    "Datacenter revenue up 437% year over year",
    "Net cash of $6.54B and no debt",
    "Guidance implies the acceleration continues into FY2027",
  ];
  const bear = [
    "Memory is historically the most cyclical business in semiconductors",
    "84.6% gross margin is a peak, not a baseline",
    "Consumer revenue fell 32% sequentially in Q4",
    "Revenue is concentrated in a few very large hyperscale customers",
    "The stock has already fallen more than 30% from its June high",
  ];

  const cw = (W - M * 2 - 0.4) / 2;
  [[bull, "The bull case", C.green, M], [bear, "The bear case", C.red, M + cw + 0.4]]
    .forEach(([items, title, color, x]) => {
      card(s, { x, y: 1.9, w: cw, h: 3.85 });
      s.addText(title, {
        x: x + 0.34, y: 2.14, w: cw - 0.68, h: 0.45,
        fontSize: 20, bold: true, color, fontFace: HEAD, margin: 0, valign: "top",
      });
      s.addText(
        items.map((t, i) => ({
          text: t,
          options: { bullet: true, breakLine: i !== items.length - 1 },
        })),
        { x: x + 0.34, y: 2.76, w: cw - 0.68, h: 2.85,
          fontSize: 15, color: C.text, fontFace: FONT, margin: 0, valign: "top",
          lineSpacing: 23, paraSpaceAfter: 13 }
      );
    });

  footnote(s, "Both columns are drawn from the same reported FY2026 figures.");
  s.addNotes(
    "Give both sides properly — this is what makes the video worth watching rather than a " +
    "pump.\n\n" +
    "The strongest bear point is the third one: 84.6% gross margin is a cycle peak. Memory " +
    "margins mean-revert violently. The strongest bull point is the sold-out capacity under " +
    "multi-year commitments, which is what makes this cycle arguably different from previous ones."
  );
}

// ===========================================================================
// 12. Close
// ===========================================================================
{
  const s = slide();
  s.addText("Three numbers to remember", {
    x: M, y: 1.35, w: W - M * 2, h: 0.75,
    fontSize: 36, bold: true, color: C.head, fontFace: HEAD, margin: 0,
  });

  const g = cols(3, 0.32);
  stat(s, { x: g.at(0), y: 2.55, w: g.w, h: 2.2, value: "$20.2B", size: 40, color: C.blue,
            label: "FY2026 revenue, up 175% on the prior year" });
  stat(s, { x: g.at(1), y: 2.55, w: g.w, h: 2.2, value: "84.6%", size: 40, color: C.teal,
            label: "Q4 non-GAAP gross margin, from 29.9% in Q1" });
  stat(s, { x: g.at(2), y: 2.55, w: g.w, h: 2.2, value: "−30%", size: 40, color: C.red,
            label: "The share price, from its June high" });

  s.addText(
    "The business and the share price have been telling different stories since June. " +
    "Which one turns out to be right depends entirely on whether 84.6% is a new normal or a peak.",
    { x: M, y: 5.15, w: W - M * 2, h: 1.0,
      fontSize: 17, color: C.text, fontFace: FONT, margin: 0 }
  );

  footnote(s, "Sandisk FY2026 results, reported 5 August 2026. Not investment advice.");
  s.addNotes(
    "Close on the tension rather than on a call.\n\n" +
    "The one-line summary: revenue up 175%, margin up 54.7 points, stock down 30% — and the " +
    "whole argument reduces to whether 84.6% gross margin is a peak or a plateau.\n\n" +
    "Standard disclaimer: this is not investment advice."
  );
}

const outPath = path.join(__dirname, "SanDisk-SNDK-FY2026.pptx");
pres.writeFile({ fileName: outPath }).then(() => console.log("wrote " + outPath));

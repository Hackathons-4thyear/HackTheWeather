# 3-minute demo video script

Written for: whoever is recording and narrating. Timings are cumulative.

**Total: 3:00.** There is no slack — rehearse once with a timer. If you overrun,
cut the Kiswahili detail at 1:55, not the backtest.

**Before recording:**

- Open the app and let every tab load fully, so nothing spins on camera.
- Confirm the banner is green (**live**). If the station is down, amber
  (*cached*) is fine — say "the station is unreachable right now, so it has
  fallen back to real cached data, and it tells you so." That failure is a
  feature; do not hide it.
- Have the **Does it work?** tab pre-scrolled to the timeline.
- Phone view ready if you can manage a second window.

---

## 0:00–0:20 — The problem (20s)

**On screen:** a tomato or potato field, or the dashboard's HIGH risk card.

> "Late blight can destroy a tomato crop in under a week. During the short
> rains, smallholders around Juja face a choice: spray on a fixed schedule and
> waste money, or wait and risk the crop.
>
> The JKUAT weather station already measures everything needed to answer this.
> Nobody was turning it into a decision."

*Delivery: flat and factual. Do not oversell.*

---

## 0:20–0:35 — What it is (15s)

**On screen:** the dashboard, top of the **Today** tab.

> "Shamba Pulse reads that station every fifteen minutes and turns it into one
> answer: is my crop at risk, and when should I spray."

---

## 0:35–1:10 — Risk and why (35s)

**On screen:** risk card, then scroll slowly through the **Why** box.

> "Today's level, and underneath it, the reasons — in plain language.
>
> 'Humidity stayed at or above 90% for eleven hours overnight.'
>
> That's the Hutton criteria: the operational standard for late blight.
> Minimum temperature above 10 degrees, six or more hours above 90% humidity.
> Two days like that in a row is a HIGH-risk warning.
>
> And HIGH means only that. If this app says HIGH, the Hutton criteria fired."

**Point at the three metric boxes** — Hutton days in a row, humid hours, Hutton
verdict.

---

## 1:10–1:40 — Spray windows (30s)

**On screen:** scroll to **When to spray**; expand one window's reasons.

> "Then: when to actually spray. Not just 'it's dry' — it needs six dry hours
> afterwards so the fungicide sticks, wind between one and four metres per
> second, leaves not wet with dew, and daylight.
>
> Notice these windows start at nine in the morning, not six. The dew has to
> burn off first — the app worked that out from the humidity forecast."

**Point at the rejection line.**

> "And it tells you what it ruled out and why. Thirty-three hours outside
> daylight. Six with wet leaves. That's not an empty list — it's an explanation."

---

## 1:40–2:00 — The SMS (20s)

**On screen:** the SMS preview, both languages side by side.

> "The farmer doesn't open a dashboard. They get this — under 160 characters,
> one SMS, English and Kiswahili."

**Read the Kiswahili line aloud if you can pronounce it.** If not:

> "The Kiswahili uses Swahili time — 'saa tatu asubuhi' for nine in the
> morning, the way people actually say it. It's badged as awaiting native
> review, because it is."

---

## 2:00–2:40 — Proof on real data (40s) ← **the most important 40 seconds**

**On screen:** the **Does it work?** tab. Let the timeline fill the frame.

> "Does it work? We replayed the real October to December 2025 short rains
> through the engine — ninety-two days of actual station data, one morning at a
> time, with the engine only seeing what had happened by then.
>
> Ten days at HIGH risk. The longest Hutton run: five days straight."

**Point at the shaded band.**

> "This shaded period is the Kenya Meteorological Department's heavy-rainfall
> advisory for late October, which named Kiambu. Our engine flagged HIGH risk
> here" — *point at 29 Oct* — "from station humidity and temperature alone. It
> had never seen that advisory.
>
> That tells us the weather was genuinely unusual. It does not tell us blight
> occurred — nobody surveyed the fields. We're careful about that difference."

**Point at the metrics.**

> "And fifteen text messages across three months, not ninety-two. A service
> that texts you every morning gets ignored."

---

## 2:40–3:00 — Data honesty and close (20s)

**On screen:** expand the **Data quality** panel.

> "One last thing. We checked the sensors instead of trusting them.
>
> The obvious rainfall field captures six percent of actual rain. Rain gauge
> two resets twenty-one times a day and is broken. Timestamps are UTC, not
> local — three hours out.
>
> All three would have produced confident, wrong advice. We found them, worked
> around them, and wrote them down."

**Back to the risk card.**

> "Real station data. Real decisions. In the farmer's language."

*Stop. Do not add a thank-you — you don't have the time.*

---

## If you have to cut

In order of what to lose first:

1. The Kiswahili explanation at 1:55 (keep showing it, drop the words).
2. The spray-window rejection detail at 1:35.
3. The third data-quality item at 2:50.

**Never cut:** the backtest numbers, or the "corroborates weather, not disease"
qualifier. The first is the strongest evidence; the second is what stops the
first from being an overclaim.

---

## Numbers you may be asked afterwards

| | |
|---|---|
| Station data | 476 days, 46,183 readings, 15-min cadence |
| Backtest period | OND 2025, 92 days, 8,737 readings |
| Coverage | 98.6–99.1%/month, no gap over 6 hours |
| HIGH days | 10 |
| Longest Hutton run | 5 days |
| Days inside a Hutton run | 21 |
| SMS sent / eligible | 15 / 46 (67% fewer) |
| Rainfall vs ERA5 | 301.8 mm vs 268.6 mm (within 12%) |
| Tests | 282 |

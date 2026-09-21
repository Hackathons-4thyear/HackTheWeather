# Devpost submission — copy-paste ready

Fields in the order Devpost's form asks for them. Every number matches the
README. Paste each block into the matching field.

**Live app:** https://shamba-pulse-jkuat.streamlit.app/
**Source:** https://github.com/Hackathons-4thyear/HackTheWeather

> ⚠️ Before pasting: open the live app in a **private window while signed out**.
> If it redirects to a sign-in page it is set to private and judges cannot open
> it. Fix in Streamlit Settings → Sharing → "This app is public and searchable".

---

## 1. Project name

```
Shamba Pulse
```

## 2. Tagline

*(Devpost allows ~200 characters. Pick one — the first is 138.)*

```
Turns the JKUAT weather station into late-blight warnings and spray timing for smallholder farmers, as an SMS in English and Kiswahili.
```

Alternative (164 chars):

```
Late-blight warnings and spray timing from the JKUAT Conduit weather station, delivered as one SMS in English and Kiswahili. Backtested on the real 2025 short rains.
```

---

## 3. About the project

### Inspiration

Late blight can take a tomato or potato crop in under a week. Around Juja, the
short rains are when it spreads — and when a smallholder can least afford to
guess.

The guess is expensive either way. Spray on a fixed weekly schedule and you buy
fungicide you may not need. Wait and watch, and by the time you see lesions it
is too late. Even when you get the week right, you can get the hour wrong:
spray before rain and it washes off; spray onto dew-wet leaves and it runs off;
spray in still air and it drifts.

What struck us was that the JKUAT Conduit weather station, sitting right there
on campus, already measures everything needed to answer both questions. The
humidity readings that tell you blight is coming were being logged every
fifteen minutes and read by nobody who needed them.

The data existed. The decision didn't.

### What it does

Shamba Pulse turns the JKUAT Conduit station's readings into two answers a
farmer can act on:

**Is my crop at risk?** We apply the **Hutton criteria**, the operational
standard for late-blight warning. A "Hutton day" needs a minimum temperature of
at least 10 °C *and* six or more hours at 90% humidity or above. Two
consecutive Hutton days is a HIGH-risk warning.

**When should I spray?** We scan the 7-day forecast for hours that are dry, will
*stay* dry for six hours so the fungicide becomes rain-fast, have wind between
1 and 4 m/s, have leaves dry enough that spray won't run off, and fall in
daylight.

Every answer carries its reasons in plain language — *"Humidity stayed at or
above 90% for 11 hours overnight"* — not a risk score with no explanation.

The output is an SMS under 160 characters, in **English and Kiswahili**:

> Shamba Pulse: HIGH blight risk for tomato/potato. Humidity stayed above 90%
> for 11h, 2 days running. Spray Wed 09:00-17:00 if you can.

> Shamba Pulse: HATARI KUBWA ya baka chelewa, nyanya/viazi. Unyevu juu ya 90%
> masaa 11, siku 2. Nyunyiza Jumatano saa 3 asubuhi-saa 11 jioni.

The Kiswahili uses **Swahili time** — *saa 3 asubuhi* for 09:00 — because that
is how the time is actually spoken. **It was written by a non-native speaker
and is awaiting native review**, which the app badges on screen.

**Two users, two products.** The farmer's product is the SMS — a smallholder
does not open a dashboard during planting season. The Streamlit dashboard is for
the people who advise many farmers: county extension officers, agrovet staff and
cooperative field teams, who need the reasoning, the forecast, the spray windows
and the evidence trail so they can answer *why*. One of them serves hundreds of
farms, which is what makes a single weather station worth building on.

**SMS integration runs in dry-run mode** — messages are composed and displayed
but **no real SMS is sent**. The Africa's Talking sender is implemented and
works against their sandbox; dry-run is the default so a public demo cannot
spend credit or text a real farmer.

### How we built it

**Python, rule-based, deliberately explainable.** No model anyone has to take on
faith — every threshold sits in one `config.py` with a comment explaining why
that number.

The pipeline: a defensive Conduit API client → a canonical column mapper → the
Hutton engine and spray-window finder → bilingual alert composition →
Africa's Talking SMS (dry-run by default) → a Streamlit dashboard. Open-Meteo
supplies the forecast, free and keyless.

**We verified the data instead of trusting it.** That turned out to matter more
than anything else we built:

- The obvious per-interval rainfall field, `rg1`, records **18.6 mm against
  301.8 mm actual** over the season — it captures **6% of rainfall**. Fed that,
  our spray adviser would have believed it never rains and recommended spraying
  into a storm. Rainfall is now reconstructed by differencing the station's
  running daily total.
- **Rain gauge 2 is faulty.** Its daily total resets about **21 times a day**
  instead of once, reconstructing to 4,365 mm in a season where ~300 mm fell.
  Our code had been using it to fill gaps in gauge 1. It no longer does.
- **Timestamps are UTC**, and Kenya is UTC+3. A naive read would shift every
  overnight humid-hours count by three hours and move the daily boundary —
  invisibly.

We cross-checked our rainfall reconstruction against ERA5 for the same
coordinates: **301.8 mm vs 268.6 mm — within 12%** over 92 days. ERA5 is a
**reanalysis** — a model reconstruction that assimilates satellite and ground
observations onto a ~9 km grid, **not a direct satellite measurement**. The
station is a single point inside one of those cells and rainfall here is
convective, which is why the day-to-day correlation is weak while the seasonal
agreement is the meaningful result. No station value is adjusted toward ERA5.

**300 tests**, concentrated on the boundaries that decide whether a farmer gets
warned: exactly 6 humid hours, exactly 10.0 °C, exactly 90.0% humidity,
non-consecutive Hutton days, and days with too many gaps to judge.

### Challenges we ran into

**The API key was the endpoint URL.** Our first live call returned `401 Wrong
Email or APIKey`. The key field contained the endpoint URL. Our client reported
the failure cleanly instead of crashing, which is how we found it in minutes
rather than hours — but it cost us a session.

**The HIGH pathway was dead and our tests didn't notice.** The backtest reported
zero Hutton days across a 90-day wet season. The cause: when assessing at 06:00,
"today" has six hours of data and is correctly unjudgeable — but our run counter
treated that as a *break*, so the consecutive count was permanently zero. HIGH
could never fire in production. Every test had used complete 24-hour days, so
all of them passed. Only replaying real time-of-day behaviour exposed it.

**Knowing when to say "I don't know."** A day with 8 of 24 hours recorded and no
humidity spike was reporting a confident LOW. That is the one verdict that tells
a farmer to relax. We worked out that coverage matters *asymmetrically*:
observing six humid hours proves six occurred however many you missed, so a
positive finding stands regardless. Observing zero in eight proves nothing about
the other sixteen — so LOW now requires real coverage, and otherwise returns
UNKNOWN.

**Kiswahili is not a translation problem.** Rendering 09:00 as "09:00" in a
Kiswahili sentence is wrong — it's *saa 3 asubuhi*, counted from dawn. And
"saa 11" means 17:00, so a duration of 11 hours had to become *masaa 11* or the
two are indistinguishable in one sentence. Swahili time is also much longer than
a 24-hour clock, which blew our 160-character SMS budget until we rewrote the
templates around it.

**Alert fatigue.** Our first version would have texted on 77% of days. A service
that texts every morning gets ignored, and an ignored alert is worth nothing on
the day it matters.

### Accomplishments that we're proud of

**It works on real data, and we can show it.** We replayed the real
October–December 2025 short rains one morning at a time, with the engine seeing
only what had happened by that point — no lookahead:

- **92 days** of real station readings, 8,737 observations
- **10 days at HIGH risk**
- **Longest Hutton run: 5 consecutive days**; **21 days inside a Hutton run**
- **15 SMS sent** under our send policy, against **46 eligible days**
- **Zero days** we had to refuse to judge

The season's main event — **29 October to 1 November 2025**, four straight days
at HIGH — **coincided with** the Kenya Meteorological Department's heavy-rainfall
advisory for 23–30 October, which named Kiambu and was expected to mark the onset
of the short rains. Our engine flagged it from station humidity and temperature
alone, with no knowledge of the advisory.

That corroborates the weather was genuinely unusual. **It does not prove blight
occurred** — nobody surveyed the fields — and we say so everywhere we mention it.

**We found three faults in the station data that would each have produced
confident, wrong advice**, and documented them with reproducible numbers rather
than quietly working around them.

**The alert policy cut texts by 67%** without hiding anything — the dashboard
still shows every day's level.

**HIGH means one thing.** Our first design let a secondary measure produce HIGH
too, which printed a HIGH headline above reasoning that said "one more day
triggers a HIGH warning." We fixed it so HIGH is reserved for the official
two-consecutive-Hutton-day trigger. If Shamba Pulse says HIGH, Hutton fired.

### What we learned

**Verify the data before building on it.** Three of our worst bugs were in
reading the sensors, not in the logic. A field named `rg1` that looks like
rainfall and returns plausible small numbers is more dangerous than one that
returns nothing, because it never looks broken.

**A broken sensor is worse than a missing one.** Rain gauge 2 produced numbers
all season. They were nonsense, and we had been using them to patch gaps in the
good gauge.

**Tests pass on the data you imagined.** Our Hutton tests were thorough and all
of them passed while the HIGH pathway was completely dead in production, because
every test used tidy complete days and real mornings are partial.

**Refusing to answer is a feature.** Encoding "insufficient data" as a real
outcome — one that breaks a Hutton run rather than bridging it — made the system
more trustworthy, not less useful.

**Localisation is domain knowledge.** Swahili time, the right word for late
blight, whether *viazi* means the potato that blight actually affects — none of
that is translation. We wrote it, flagged it as unreviewed, and listed the
specific questions for a native speaker, because shipping unreviewed Kiswahili
to farmers as though it were finished would be worse than shipping English.

### What's next for Shamba Pulse

**A farmer pilot with a county extension office.** Put the SMS in front of real
smallholders through the people who already advise them. Everything about the
farmer experience is currently reasoned from constraints rather than tested.

**Calibrate against observed outbreaks in Kiambu.** The Hutton criteria were
developed in the UK and encode a temperate understanding of *P. infestans*.
Kenyan highland conditions, local strains and local cultivars may shift the
thresholds. Pairing extension-officer or farmer-reported blight records with our
risk history is the single most valuable thing we could do.

**CHIRPS / GPM satellite rainfall in the live pipeline.** Today satellite-derived
data appears only as an offline ERA5 sanity check. Bringing gridded rainfall into
the running system would fill station gaps, flag sensor drift automatically, and
extend coverage beyond the single point we can currently speak for.

**Multi-station coverage.** One point cannot represent a county; convective rain
soaks one field and misses the next.

**An ML model trained on observed blight outbreaks.** `disease_engine.assess()`
is a documented swap point: same input frame, same output contract, so a learned
model can replace the rules without touching a single caller. It needs labels
first, which is why it follows calibration rather than leading.

**Native Kiswahili review**, then we flip the flag the app uses to badge it.

**USSD**, so a farmer can *ask* rather than wait, with no smartphone and no data
bundle.

---

## 4. Built with

```
python, pandas, numpy, streamlit, plotly, pyarrow, requests, africas-talking, open-meteo, era5, jkuat-conduit-weather-station, hutton-criteria, pytest
```

---

## 5. Try it out

```
https://shamba-pulse-jkuat.streamlit.app/
https://github.com/Hackathons-4thyear/HackTheWeather
```

---

## 6. Image gallery — upload in this order

| # | File | Suggested caption |
|---|---|---|
| 1 | `docs/img/01-risk-card.png` | Today's blight risk with the plain-language reasons behind it. The amber banner is the app reporting that the station's newest reading is 13 hours old rather than calling stale data "live". |
| 2 | `docs/img/02-spray-and-sms.png` | Ranked spray windows with what was ruled out and why, and the exact SMS a farmer would receive in English and Kiswahili. |
| 3 | `docs/img/03-backtest-timeline.png` | The real OND 2025 short rains replayed day by day: 92 days, 10 HIGH-risk days, 15 messages. The shaded band is the KMD heavy-rainfall advisory. |
| 4 | `docs/img/04-era5-crosscheck.png` | Our reconstructed rainfall cross-checked against ERA5 reanalysis — 301.8 mm vs 268.6 mm over the season. |

**Devpost uses the first image as the thumbnail**, so upload `01-risk-card.png`
first.

---

## Pre-submit checklist

- [ ] Live app opens in a **private window while signed out** (not private/gated).
- [ ] App is awake — Community Cloud sleeps apps after ~7 days idle.
- [ ] Re-read *Inspiration* and make it sound like your team.
- [ ] Add team members on Devpost.
- [ ] Upload the four screenshots in the order above.
- [ ] If a native speaker reviews the Kiswahili before the deadline, say so and
      soften the "awaiting review" wording.
- [ ] Decide whether to send `docs/note_to_organizers_gauge2.md` to JHUB. If you
      do and they confirm the fault, mention it in *Accomplishments*.

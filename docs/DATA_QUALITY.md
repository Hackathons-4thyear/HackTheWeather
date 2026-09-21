# Data quality — JKUAT Conduit station

What we found when we actually looked at the data, and what we did about it.

Every number here is measured, not assumed. Reproduce them with:

```bash
.venv/Scripts/python.exe analysis/explore_data.py data/conduit_history.parquet
.venv/Scripts/python.exe analysis/validate_rain.py
```

**Period examined:** 2025-10-01 → 2026-01-01 (the OND 2025 short rains),
8,737 readings, plus a September 2026 sample for the live-API checks.

---

## Summary

| Finding | Severity | What we did |
|---|---|---|
| `rg1` captures only 6% of rainfall | **Critical** | Derive rain from `rg1tt` instead |
| Rain gauge 2 (`rg2`/`rg2tt`) is faulty | **Critical** | Never used, never a fallback |
| Timestamps are UTC, not local | **Critical** | Convert to Africa/Nairobi on ingest |
| `wind_gust_dir` duplicates `wind_gust` | Moderate | Not used |
| `si1145_uv` reads a constant zero | Minor | Not used (we never claimed UV) |
| No soil-moisture sensor exists | By design | Never fabricated |

Three of these would have produced confidently wrong farmer advice if we had
trusted the obvious field names.

---

## 1. Rainfall: `rg1` is not usable, `rg1tt` is

The station exposes several rain fields. The obvious choice, `rg1`, looks like
per-interval rainfall in mm. It is not trustworthy.

Measured over OND 2025 (8,737 readings):

| Field | Total over the season |
|---|---|
| `rg1` (per-interval) | **18.6 mm** |
| `rg1tt` differenced (what we use) | **301.8 mm** |

**`rg1` captures 6.2% of the rainfall that actually fell.** The same test on a
September 2026 sample gave 0.20 mm against 6.20 mm — about 3%.

### Why this mattered

The spray-window advisor refuses to recommend spraying unless it will stay dry
for six hours afterwards. Fed `rg1`, it would have believed the station sits in
a near-desert and cheerfully recommended spraying into an oncoming storm —
washing the fungicide off and wasting the farmer's money on the exact day the
disease risk was highest.

### What `rg1tt` is

A **running daily total** that resets at UTC midnight. Rainfall per interval is
the difference between consecutive readings, and a drop back toward zero is the
reset — not negative rain. Implemented in
`services/data_processor.rainfall_from_cumulative()`.

Worked example from 2026-09-18:

```
13:13  rg1tt 0.20   ->  +0.20 mm
13:28  rg1tt 1.40   ->  +1.20 mm
13:43  rg1tt 1.60   ->  +0.20 mm
15:16  rg1tt 3.80   ->  +2.20 mm
15:31  rg1tt 4.40   ->  +0.60 mm
17:33  rg1tt 4.80   ->  +0.40 mm
```

Over the same window `rg1` recorded 0.20 mm total.

`rg1tp` appears to hold the **previous day's** total (it takes yesterday's
`rg1tt` value at the daily rollover). We do not use it.

### Independent check

We compared our reconstruction with ERA5 reanalysis from the Open-Meteo
archive, same coordinates, OND 2025:

| | Station (ours) | ERA5 |
|---|---|---|
| Season total | 301.8 mm | 268.6 mm |
| Mean per day | 3.28 mm | 2.92 mm |

**Ratio 1.12** — 12% apart on the seasonal total. Daily correlation is modest
(Pearson r 0.255, Spearman r 0.405; rainy-day agreement 57/92 = 62%,
Jaccard 0.38).

**The weak daily correlation is the expected result, not a defect.** ERA5 is a
~9 km grid-cell average; the station is a single point inside it. Rainfall here
is convective — a storm can soak one field and miss the next. A grid average
also smears one storm into small amounts across several days, which is why ERA5
called 30 days rainy that the station did not, while the station called only 5
that ERA5 did not.

What the check establishes is that the reconstruction is not *structurally*
wrong: a method that double-counted, missed resets, or was off by an order of
magnitude would not land within 12% of an independent record over 92 days.

**This is a credibility check, not calibration.** No station value is adjusted
toward ERA5, and nothing from this comparison enters the decision engine.

---

## 2. Rain gauge 2 is faulty

Gauge 2 should corroborate gauge 1. It cannot.

Measured over the same 93 days:

| Field | Behaviour |
|---|---|
| `rg2` | Constant zero across all 8,737 readings |
| `rg2tp` | Constant zero |
| `rg2tt` | **1,962 resets — 21.1 per day** |

A daily running total resets **once per day**. `rg2tt` resets about 21 times a
day, and differencing it the same way as gauge 1 reconstructs to
**4,365 mm** for a season in which roughly 300 mm fell — an order of magnitude
and a half too high.

In an 11-day dry period in September 2026, `rg2tt` reconstructed to 297 mm
while essentially no rain fell.

### What we did

Gauge 2 is never used, and specifically **never used to fill gaps in gauge 1**.
An earlier version of `data_processor.py` did exactly that. It is now gated
behind `RAIN_GAUGE_2_TRUSTED = False`, with a test asserting the flag stays
false and another asserting gauge 2 values cannot leak into `rain_mm`.

> A broken sensor is more dangerous than a missing one, because it looks like
> data.

---

## 3. Timestamps are UTC, not local time

The API returns `"ts": "2026-09-20T00:00:26Z"`. The trailing `Z` is UTC. Kenya
is **UTC+3** year-round.

Read naively as local time, every reading lands three hours early. That would:

- shift the overnight humid-hours window, the core Hutton input, by 3 hours;
- move the daily boundary, so hours from one day are counted against another;
- misplace spray windows relative to the daylight limits.

None of it would be visible as an error — just quietly wrong advice.

We convert to `Africa/Nairobi` on ingest and carry tz-naive local time
throughout (`data_processor.parse_timestamps`). A test pins
`00:00:26Z → 03:00:26` local.

One visible consequence: the station's daily rain counter resets at UTC
midnight, which is **03:00 local**. Daily rainfall totals in our data are
therefore local-calendar sums of the reconstructed increments, not the raw
counter's own daily cycle.

---

## 4. `wind_gust_dir` is not a direction

`wind_gust_dir` is **identical to `wind_gust` on 100% of rows**, and ranges
−999.9 to 13.9. A direction field should span 0–360°. It appears to be a
duplicated gust-speed field, including one `-999.9` sentinel value.

We do not use it. `wind_dir` (0–359) is a genuine direction and is mapped, but
the decision engine does not currently need it.

---

## 5. Constant fields

Across all 8,737 OND readings these never change:

- `si1145_uv` — constant zero. The SI1145 UV channel reports nothing.
- `rg2`, `rg2tp` — see gauge 2 above.

We map `si1145_uv/ir/vis` as **raw counts** and label them that way. They are
**not** calibrated W/m², and we never present them as irradiance.

---

## 6. Coverage and gaps

OND 2025 is unusually clean:

| Month | Readings | Coverage |
|---|---|---|
| 2025-10 | 2,935 | 98.6% |
| 2025-11 | 2,853 | 99.1% |
| 2025-12 | 2,937 | 98.7% |

- Missing **1.1%** of a perfect 15-minute grid.
- **Zero gaps longer than 6 hours.**
- Actual cadence is **15 min 5 s**, drifting a few seconds per reading rather
  than locking to the wall clock. Harmless, but it means timestamps do not fall
  on neat quarter-hours, so we resample by time rather than assuming a grid.

### How gaps are handled

The engine never guesses across a gap:

- A day missing more than **25%** of its hours is marked *insufficient data*
  and is **not** judged (`disease_engine.MAX_MISSING_FRACTION`).
- Such a day **breaks** a consecutive-Hutton run rather than being bridged.
- Hourly resampling leaves an empty hour as `NaN`, never as a confident 0 mm of
  rain.
- The rolling humid-hours measure treats coverage **asymmetrically**: observing
  6 humid hours proves at least 6 occurred however many were missed, so a
  positive finding stands; observing zero in 8 of 24 proves nothing about the
  other 16, so a LOW verdict requires real coverage and otherwise returns
  UNKNOWN.

---

## 7. What the station does not measure

**There is no soil-moisture sensor.** There is no leaf-wetness sensor either.

We use relative humidity ≥ 90% as the leaf-wetness proxy, which is what the
Hutton criteria are defined on, and we say so. We do not model soil water, and
no output implies we do.

---

## Fields we map

| Canonical | Raw | Note |
|---|---|---|
| `timestamp` | `ts` | UTC → Africa/Nairobi |
| `temperature_c` | `temp_sht` | Preferred over `temp_bmx`/`temp_mcp`: same chip as the humidity sensor, so temp and RH agree in the Hutton test |
| `humidity_pct` | `humidity_sht` | |
| `rain_mm` | `rg1tt` differenced | **not** `rg1` |
| `wind_speed_ms` | `wind_spd` | |
| `wind_gust_ms` | `wind_gust` | |
| `wind_dir_deg` | `wind_dir` | |
| `pressure_hpa` | `press_bmx` | ~850 hPa at 1,524 m |
| `wet_bulb_c` | `wet_bulb_temp` | |
| `uv_raw`, `ir_raw`, `vis_raw` | `si1145_*` | raw counts, not W/m² |

Present but unused: `rg1`, `rg2`, `rg1tp`, `rg2tp`, `rg2tt`, `temp_bmx`,
`temp_mcp`, `wind_gust_dir`, `heat_idx`, `wet_bulb_globe_temp`.

# Shamba Pulse — Project Context

Built for **Hack The Weather 2026** (JHUB Africa, JKUAT, Kenya). Deadline is tight:
**prioritise a working, demo-ready MVP over completeness.**

## What the product is

Shamba Pulse turns real data from the JKUAT Conduit weather station into farm
decisions for smallholder farmers around Juja / Kiambu, targeted at the El Niño
short-rains season (Oct–Dec 2026: above-average rain, prolonged wet spells).

Pipeline:

```
Conduit station data (+ free forecast) -> disease-risk & spray-window engine
  -> clear decisions -> SMS/WhatsApp-style alerts (Kiswahili + English) -> impact
```

## Core features, in priority order

1. **Crop disease risk** for tomato/potato late blight via the **Hutton criteria**:
   a *Hutton day* = min temperature >= 10 degC **AND** >= 6 hours with RH >= 90%.
   **Two consecutive Hutton days = HIGH risk.**
   Also compute a simpler **"humid hours" risk level** (LOW / MODERATE / HIGH) so
   there is always an explainable output even when Hutton data is incomplete.
2. **Spray-window advisor** — best upcoming windows to spray fungicide:
   no rain in the next ~6 h, wind speed in a moderate band (~1–4 m/s: not calm,
   not gusty), no rain right now. All thresholds live in `config.py` **with
   comments explaining why each number was chosen.**
3. **Explainability** — every alert carries plain-language reasons, e.g.
   *"Humidity stayed above 90% for 8 hours last night."*
4. **Alerts** — short SMS text (**< 160 chars**) in **both English and Kiswahili**.
   Africa's Talking sender working in sandbox mode via env vars, plus a
   **dry-run mode that just prints the message**.
5. **Backtest** — replay historical Conduit data, show which alerts *would* have
   fired and when. Proof the engine works on real data.
6. **Dashboard (Streamlit)** — current conditions, disease-risk level + reasons,
   next spray windows, 7-day outlook, alert preview, backtest timeline (Plotly).
   Clean, mobile-friendly, farmer-focused. **Not a generic weather app.**

## Data facts (verify, never assume)

- Conduit station records **every 15 minutes**: two rain gauges, several
  temperature sensors (BMX, MCP, SHT), wet bulb, WBGT, humidity (SHT),
  pressure (BMX), wind speed / direction / gust, and SI1145 UV / IR / visible
  light as **raw counts, NOT calibrated W/m^2**.
- **There is NO soil-moisture sensor. Never fabricate missing variables.**
- Rainfall is **heavily zero-inflated** (most intervals are 0).
- Reported API contract (**unconfirmed — verify**):
  `POST https://conduit.jhubafrica.com/data.php`, form-encoded fields
  `apikey`, `email`, `fromdate` (YYYY-MM-DD), `todate` (YYYY-MM-DD)
  -> JSON for that date range. **Response shape is unknown: parse defensively.**
- Historical exports live in `data/` as CSV or XLSX with raw column names like
  `ts`, `rg1`, `rg2`, `temp_bmx`.
- **Forecast:** Open-Meteo API (free, no key) hourly 7-day forecast at JKUAT
  (lat -1.0914, lon 37.0147): temperature, relative humidity, precipitation,
  wind speed.

## Hard rules

- **Real Conduit data must be visibly used — 25% of the judging score.**
  Mock data is a fallback only and must be clearly labelled **"DEMO DATA"** in the UI.
- Credentials come **only** from `.env`: `CONDUIT_API_KEY`, `CONDUIT_EMAIL`,
  `AT_USERNAME`, `AT_API_KEY`. `.env.example` is committed; `.env` is gitignored.
- **If the live API fails the app must not crash** — show a status banner and fall
  back to the latest historical data.
- Keep the engine **rule-based and explainable**, structured so an ML model can
  drop in later (same input frame, same output contract).
- Python 3.11 was specified; **this machine has 3.13.4**, which all deps support.
  Virtualenv at `.venv/`. Keep dependencies minimal.

## Structure

```
shamba-pulse/
  app.py                        # Streamlit entry point
  config.py                     # ALL thresholds, with explanatory comments
  data/                         # historical Conduit exports (CSV / XLSX)
  services/
    conduit_api.py              # live station API client (defensive parsing)
    data_processor.py           # raw -> canonical column mapping, resampling
    forecast.py                 # Open-Meteo hourly forecast
    disease_engine.py           # Hutton criteria + humid-hours risk
    spray_window.py             # spray-window finder
    alerts.py                   # EN + SW SMS text generation
    sms_sender.py               # Africa's Talking, sandbox + dry-run
  analysis/
    explore_data.py             # inspect whatever is in data/
    backtest.py                 # replay history, show alerts that would fire
  tests/                        # unit tests for Hutton + spray-window logic
  README.md
  CLAUDE.md
```

## Canonical column names

`data_processor.py` maps raw station columns onto these names. Everything
downstream uses only these:

| canonical | meaning | unit |
|---|---|---|
| `timestamp` | reading time (tz-aware, Africa/Nairobi) | — |
| `temperature_c` | air temperature | degC |
| `humidity_pct` | relative humidity | % |
| `rain_mm` | rainfall in the interval | mm |
| `wind_speed_ms` | wind speed | m/s |
| `wind_gust_ms` | wind gust | m/s |
| `wind_dir_deg` | wind direction | deg |
| `pressure_hpa` | barometric pressure | hPa |
| `wet_bulb_c` | wet-bulb temperature | degC |
| `uv_raw`, `ir_raw`, `vis_raw` | SI1145 **raw counts**, uncalibrated | counts |

## Working agreement

- Work in **small steps, run the code after each step.**
- Ask before making big design changes.
- After each major step: short summary of what works and what's next.
- Say clearly when something is needed from the user (API keys, data files).

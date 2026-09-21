# Deploying to Streamlit Community Cloud

Written for whoever is doing the clicking. Takes about 10 minutes.

**Live app: https://shamba-pulse-jkuat.streamlit.app/**

## What actually worked (read this first)

Our first deploy hung on *"Your app is in the oven"* for over an hour. What
fixed it:

1. **Delete the stuck app.** A wedged build does not reliably recover in place;
   Reboot was not enough. Remove it from share.streamlit.io and start again.
2. **Set Python 3.12 in Advanced settings _before_ clicking Deploy.** This is
   the single most important step. `numpy==2.5.3` requires Python >= 3.12, so
   anything lower makes the dependency set unsatisfiable.
3. **Ignore `runtime.txt`.** Community Cloud does not read it - we removed ours.
   The Advanced-settings dropdown is the only place the version is set.
4. **Set the app to Public.** A new app can default to private, which redirects
   visitors to a login page (HTTP 303 to `share.streamlit.io/-/auth/app`).
   Judges cannot open a private app. Settings -> Sharing -> **"This app is
   public and searchable"**.

**The short version:** push to GitHub, point share.streamlit.io at `app.py`,
paste two secrets. The app works even if you skip the secrets — it falls back
to the 476 days of real station data committed in this repo.

---

## Before you start

You need:

- a **GitHub** account with this repo pushed to it, and
- a **Streamlit Community Cloud** account (free, sign in with GitHub) at
  <https://share.streamlit.io>.

Files already in the repo that make this work — nothing to create:

| File | Purpose |
|---|---|
| `requirements.txt` | Pinned to the exact versions the tests ran on |
| `.streamlit/config.toml` | Theme, so cloud matches local |
| `data/conduit_history.parquet` | 476 days of real data (0.81 MB) so the app works offline |
| `app.py` | The entry point |

---

## Step 1 — Push to GitHub

```bash
git remote add origin https://github.com/Hackathons-4thyear/HackTheWeather.git
git push -u origin main
```

**Before pushing, confirm no secrets are going with it:**

```bash
git ls-files | grep -E "^\.env$|secrets\.toml$"
```

This must print **nothing**. Both are gitignored; this verifies it.

The repo can be **public or private** — Community Cloud handles both.

---

## Step 2 — Create the app

1. Go to <https://share.streamlit.io> and sign in with GitHub.
2. Click **"Create app"** (top right).
3. Choose **"Deploy a public app from GitHub"**.
4. Fill in:
   - **Repository:** `Hackathons-4thyear/HackTheWeather`
     *(or your fork, if you deployed from one - see "Deploying from a fork" below)*
   - **Branch:** `main`
   - **Main file path:** `app.py`
   - **App URL:** pick your subdomain, e.g. `shamba-pulse`
5. **Click "Advanced settings" and set Python version to 3.12.** This is not
   optional. Community Cloud does **not** read `runtime.txt` or
   `.python-version` - the dropdown is the only place the version is set, and
   the default is 3.12.

   **3.12 or 3.13 both work. 3.11 and below will FAIL**, because
   `numpy==2.5.3` requires Python >= 3.12 and pip will not find an installable
   version. Verified: every pin installs and imports cleanly in a fresh 3.12
   environment, and the app runs there in about 14 seconds cold.

6. Click **"Deploy"**.

First build takes **3–5 minutes**. Nothing compiles from source — every pin has
a prebuilt Linux wheel — so this is download and unpack time. You will see the
build log stream. Leave it alone.

**The app will work at this point, even with no secrets.** It falls back to the
committed history and the banner reads *"Cached station history (real Conduit
data, not live)"*.

---

## Step 3 — Add the secrets (for live station data)

This is what switches the banner from *cached* to **live**.

1. On your deployed app, click the **⋮ menu** (bottom right) →
   **"Settings"**.
   *(Or from <https://share.streamlit.io>: find the app → ⋮ → "Settings".)*
2. Open the **"Secrets"** tab.
3. Paste this, replacing the values with your real ones:

```toml
CONDUIT_API_KEY = "your_actual_conduit_key"
CONDUIT_EMAIL = "your_registered_email@example.com"
```

4. Click **"Save"**.

The app reboots automatically (~30 seconds). The banner should turn green:
**"Live station data"**.

### Format warnings

- **TOML, not `.env`.** Values need **double quotes**. `KEY=value` will not
  parse.
- **No trailing spaces** inside the quotes.
- The key is the **key**, not the API URL. (We lost an hour to exactly this —
  the endpoint URL had been pasted into the key field, and the server correctly
  answered `401 Wrong Email or APIKey`.)

### Optional — SMS

Only add these if you want to demo the Africa's Talking sandbox:

```toml
AT_USERNAME = "sandbox"
AT_API_KEY = "your_at_sandbox_key"
SMS_DRY_RUN = "true"
```

**Leave `SMS_DRY_RUN = "true"` on a public demo.** With it false, anyone
opening your URL could spend your SMS credit. The app defaults to dry-run even
if you set nothing.

---

## Step 4 — Check it

Open your URL and confirm:

- [ ] Banner reads **"Live station data"** (green) — or *"Cached station
      history"* (amber) if you skipped the secrets. **If it says "DEMO DATA" in
      red, something is wrong** — see troubleshooting.
- [ ] Forecast banner reads **"7-day forecast is live"**.
- [ ] **Today** tab shows a risk card with reasons.
- [ ] **This week** shows the humidity chart with the 90% line.
- [ ] **Does it work?** shows the backtest timeline with the shaded KMD band.
- [ ] SMS preview shows English and Kiswahili side by side, with the
      *"Kiswahili not yet reviewed"* badge.
- [ ] Open it on a phone. The layout is built for a 400px screen.

---

## Troubleshooting

**Banner says "DEMO DATA"**
Neither live nor cached data loaded. Check `data/conduit_history.parquet` is
actually committed (`git ls-files data/`) — it is 0.81 MB and must not be
caught by a `.gitignore` rule. Expand **"What we tried"** on the app; it names
the reason each source failed.

**Banner says "cached" when you expected live**
Expand **"What we tried"**. If it says *"CONDUIT_API_KEY / CONDUIT_EMAIL not
set"*, the secrets did not save or are misspelled — names are case-sensitive.
If it says `HTTP 401`, the credentials are wrong. If it says a network error,
the station is down and the fallback is doing its job.

**Build fails on `pip install`, or the app sits on "Your app is in the oven"**
Almost always the Python version. Check Advanced settings: anything below 3.12
cannot install `numpy==2.5.3`. Set it to **3.12**, then use **Reboot app** -
changing the setting alone does not always rebuild the environment. If that
does not clear it, delete the app and redeploy; a wedged build sometimes will
not recover in place.

All eight pins were checked against PyPI and have manylinux wheels for cp312
and cp313, so nothing needs compiling from source.

**App is slow on first load**
Expected, but bounded: about 14 seconds in a clean environment. It calls the
station API (30 s timeout) and Open-Meteo (20 s timeout), then replays the
92-day backtest (~3 s). Worst case, if both APIs hang until they time out, the
first load takes under a minute and still renders from cached data. Results are
cached (`@st.cache_data`, 10–60 min TTLs), so later loads are fast. The cache
clears on reboot.

**"App is over its resource limits"**
Community Cloud allows 1 GB RAM. The backtest over the full history is the
heaviest step. Reboot from the ⋮ menu; if it recurs, limit the backtest with
`bt.run_backtest(hist, start=..., end=...)` to the OND 2025 season only.

---

## Deploying from a fork

If the app is deployed from **your fork** rather than from
`Hackathons-4thyear/HackTheWeather`, Streamlit watches the fork, not the org
repo. Pushes to the org repo will **not** reach the live app on their own.

After anything is pushed to the org repo:

1. Open your fork on GitHub.
2. Click **"Sync fork"** -> **"Update branch"**.
3. Streamlit redeploys within a minute or two.

If the app is deployed straight from the org repo, ignore this section - pushes
go live automatically.

---

## Making sure judges can actually open it

Check it in a **private/incognito window**, signed out of Streamlit and GitHub.

- Page loads -> public, judges are fine.
- Redirected to a sign-in page -> the app is **private**. Fix it in
  Settings -> **Sharing** -> "This app is public and searchable".

From a terminal, a public app answers `200`; a private one answers `303` with a
`location:` header pointing at `share.streamlit.io/-/auth/app`:

```bash
curl -s -D - -o /dev/null https://shamba-pulse-jkuat.streamlit.app/
```

---

## Updating a deployed app

Push to `main`. Community Cloud redeploys automatically.

`requirements.txt` changes trigger a full rebuild (3–5 min); code-only
changes are much faster.

**Secrets survive redeploys** — you only enter them once.

---

## Keeping it awake for judging

Community Cloud sleeps apps after ~7 days idle. A sleeping app shows a
*"Yawn, this app has gone to sleep"* page and takes ~30 seconds to wake.

**Open the app yourself the morning of judging.** That is the whole trick.

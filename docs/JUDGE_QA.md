# Judge Q&A preparation

Fifteen questions the panel is most likely to ask, with honest answers.

**Rule for the whole session: if you do not know, say so and say what you would
do to find out.** Every genuine weakness below is already written into the
README, so a judge who finds one has found something we disclosed, not
something we hid. That is a much better position than bluffing.

---

## Judge A — academic, technical rigour

**1. "The Hutton criteria were developed in Scotland. Why should they hold in
Kiambu?"**

They shouldn't be assumed to. Hutton encodes a temperate understanding of
*P. infestans*, and Kenyan highland conditions, local strains and local
cultivars could all shift the thresholds. We use it because it is the
established operational standard and it is transparent — every threshold is one
line in `config.py`. Calibrating against observed Kiambu outbreaks is the first
item on our roadmap, and until that happens the thresholds are borrowed, not
validated here.

**2. "Your backtest has no ground truth. What does it actually demonstrate?"**

Exactly what we claim and nothing more: that on 92 days of real station data
the engine fires on a defensible, reproducible schedule — 10 HIGH days, not 92
and not zero — and that it does so without lookahead, which we enforce in code
and assert in tests. It demonstrates the engine behaves correctly. It does not
demonstrate that blight occurred, and we never say it does.

**3. "How do you know your no-lookahead claim is real?"**

There is one line that slices history at each simulated alert time. Two tests
guard it: one spies on every frame handed to the engine and asserts its maximum
timestamp never exceeds the alert time, and another truncates the history after
day eight and asserts days one to eight produce identical verdicts. If future
data leaked in, the second test would fail.

**4. "You have 282 tests. Did they catch anything real, or are they decoration?"**

They caught three bugs that would have shipped. The most serious: our tests all
passed while the HIGH pathway was completely dead in production. At a 06:00
alert, "today" has six hours of data and is correctly unjudgeable — but our run
counter treated that as a break, so the consecutive count was permanently zero.
Every test used tidy 24-hour days. Only replaying real time-of-day behaviour
exposed it, which is why the backtest is a test and not a demo.

**5. "Why rules instead of machine learning?"**

Two reasons. We have no labelled outbreaks, so there is nothing to train on —
an ML model here would be fitting weather to weather. And a farmer spending
money on fungicide deserves a reason, which is why every output carries plain
language. `disease_engine.assess()` is a documented swap point: same input
frame, same output contract, so a model can replace the rules without touching
a single caller once labels exist.

---

## Judge B — innovation hub, impact and adoption

**6. "How does a farmer actually receive this? Who pays for the SMS?"**

Africa's Talking, integrated and working in sandbox — messages are under 160
characters so each costs one segment. We have not solved who pays. The
realistic routes are a county agriculture office, a cooperative, or an
agro-dealer bundling it with inputs, and the send policy matters here: 15
messages per season per farmer is an affordable unit cost in a way that daily
messaging is not.

**7. "Have you spoken to any farmers?"**

No, and that is our biggest gap. Everything about the farmer experience —
the 160-character limit, Kiswahili, Swahili time, sending only on escalation —
is reasoned from constraints rather than tested with users. The first thing we
would do with more time is sit with ten farmers in Juja and watch them read
these messages.

**8. "What stops a farmer ignoring this after two false alarms?"**

That is precisely why the send policy exists. Our first version would have
texted on 77% of days; the policy cuts that to 15 messages across a season by
sending only on escalation, with a cooldown, and never for LOW. We also never
say HIGH unless the Hutton criteria actually fired — that restraint is what
makes the word mean something. Whether it survives contact with real farmers is
untested.

**9. "This covers one station. How is that a product?"**

It isn't yet. One point cannot represent a county — convective rain soaks one
field and misses the next. The path is more Conduit nodes, or blending station
readings with satellite humidity to interpolate between them. What we have
built that does scale is the pipeline: swap the data source and the engine,
alerts and policy work unchanged.

**10. "What would you do with three months and funding?"**

Calibration first — pair extension-officer blight reports with our risk history
and tune the thresholds to Kiambu. In parallel, a native Kiswahili review and
a ten-farmer usability study. Then a second station to test whether risk
interpolates sensibly between two points. Scaling before calibrating would just
distribute unvalidated advice faster.

---

## Judge C — data and climate practitioner

**11. "Show me you actually understand the Conduit data."**

The obvious rainfall field, `rg1`, records 18.6 mm against 301.8 mm actual over
the season — it captures 6%. Rain gauge 2's daily total resets about 21 times a
day instead of once and reconstructs to 4,365 mm in a ~300 mm season; it is
broken, and we had been using it to patch gaps in the good gauge until we
checked. Timestamps are UTC while Kenya is UTC+3, which would have shifted
every overnight humid-hours count by three hours. All three are documented with
numbers in `docs/DATA_QUALITY.md`.

**12. "Your rainfall is reconstructed. How do you know it's right?"**

We cross-checked it against ERA5 reanalysis for the same coordinates: 301.8 mm
against 268.6 mm over 92 days, within 12%. That is the check that matters — a
method that double-counted or missed resets would not land that close over
three months. Daily correlation is modest (Spearman 0.41), which is the
physically expected result for a point gauge against a ~9 km grid cell in
convective rain, not a defect. It is a credibility check, not calibration; no
station value is adjusted toward ERA5.

**13. "You cite the KMD advisory. Isn't that circular — rain causes both?"**

It would be circular if we presented it as validating the disease model, which
is why we don't. Our engine flagged that period from station humidity and
temperature with no knowledge of the advisory, so it corroborates that the
weather was genuinely unusual and that we reacted to signal rather than noise.
It says nothing about whether blight occurred, because nobody surveyed the
fields. We state that qualifier everywhere the advisory appears.

**14. "Humidity above 90% is a poor proxy for leaf wetness. Why should I trust
it?"**

It is a proxy, and we say so. The station has no leaf-wetness sensor and we
refuse to invent one — same reason we model no soil moisture despite it being
the obvious next feature. The 90% threshold is not ours; it is the Hutton
definition, which was built around this exact substitution. It is the best
available given the instrument, and it is a real limitation, not a solved one.

**15. "What happens when the station goes down mid-season?"**

Three layers. The client never raises — it returns a result object carrying the
error, and the dashboard shows a banner naming what failed. It falls back to
the committed 476-day history and labels it cached real data, not live. And the
engine refuses to guess: a day missing more than 25% of its hours is marked
insufficient and *breaks* a Hutton run rather than bridging it, because a
missing night is exactly when the humid hours would have happened.

---

## Two questions we cannot answer well

Be honest rather than inventive if these come up.

**"What yield or income improvement does this deliver?"**
We don't know. We deliberately publish no yield or money figures because we
have not run a field trial and would be making them up. Our impact section is
labelled an illustrative scenario and compares 13 weekly scheduled sprays with
6 episode-targeted ones — that is a spray-count comparison, not an outcome
claim.

**"Is the Kiswahili correct?"**
Unreviewed, and badged as such in the app. A non-native speaker wrote it. We
handled the parts that are domain knowledge rather than translation — Swahili
time, `masaa` versus `saa` for durations — and wrote down the specific open
questions, including whether *viazi* reads as the potato blight actually
affects. Shipping it as finished would be worse than shipping English.

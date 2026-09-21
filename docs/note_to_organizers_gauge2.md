# DRAFT — note to the JHUB Africa / Conduit team

**Not sent.** Review, edit and decide whether to send. Suggested channel: the
hackathon support/mentors channel, or whoever maintains the station.

Written for: the people who run the Conduit station, who will want the
reproducible detail and not the hackathon framing.

---

**Subject: Possible fault on rain gauge 2 at the JKUAT station**

Hello,

We are one of the Hack The Weather teams working with the JKUAT Conduit station
data. While validating rainfall for our project we think we have found a fault
on the second rain gauge, and it seemed worth passing on in case it is useful
to you.

**What we are seeing**

Over 2025-10-01 to 2026-01-01 (8,737 readings):

- `rg2` and `rg2tp` are constant zero across every reading in the period.
- `rg2tt`, which otherwise behaves like a running daily total, decreases
  **1,962 times** — about 21 times a day. A daily total would be expected to
  reset once a day.

Treating `rg2tt` as a running total and summing its increments the same way we
do for gauge 1 gives roughly **4,365 mm** for the season, against about
**302 mm** from gauge 1 over the same period.

We also checked a dry spell in September 2026 (2026-09-10 to 2026-09-20): gauge
1 recorded 6.2 mm, while the same method on `rg2tt` produced 297 mm.

Gauge 1 looks healthy. Its seasonal total is within about 12% of ERA5
reanalysis for the same coordinates (302 mm vs 269 mm), which is what we would
expect for a point gauge against a ~9 km grid cell.

**Two smaller things, in case they are related**

- `wind_gust_dir` is identical to `wind_gust` on 100% of rows and ranges
  −999.9 to 13.9, so it looks like a duplicated gust-speed field rather than a
  direction. There is one −999.9 value that may be a sentinel.
- `si1145_uv` is constant zero throughout, while the `ir` and `vis` channels on
  the same sensor do vary.

**One question**

Could you confirm what `rg1` is meant to represent? Over the same period it
totals 18.6 mm against 301.8 mm from `rg1tt`, so we have been deriving rainfall
by differencing `rg1tt`. If `rg1` is intended as something other than
per-interval rainfall we would rather use it correctly than work around it.

We are not blocked on any of this — we have worked around it on our side. We
are only flagging it in case the hardware needs attention or other teams hit the
same thing.

Happy to share the exact queries or a short script if that would help.

Thanks for making the station available to us.

— [team name], Hack The Weather 2026

# STATUS — where the project is right now

*Last updated: 2026-10-27 (session that continued the failed "18z AIFS" task.)*

This file is the hand-off note. If a session dies (context limit, sandbox
reset), read this first, then `git log --oneline -5`.

## Live

- Site: **https://massachusettswx-weather.github.io/Forecast-charts/**
- Repo: `git@github.com:massachusettswx-weather/Forecast-charts.git` (public, `main`)
- Publishes itself: a 15-minute poll plus a deadline sweep at 03/09/15/21 UTC
  (9 h after each model initialisation). The runner fetches, renders, commits the
  store and deploys Pages on its own — **the sandbox is no longer in the loop**.

## What was done in the last session (2026-10-27)

1. **Cycle label fixed.** `render.py` used `strftime("%dz")` → `%d` is the *day*,
   so every chart said "26z 26 Oct 2026" instead of "18z 26 Oct 2026". The label
   now lives in `render.cycle_label()` and the store carries it as
   `latest.json → cycle_label`.
2. **06z/18z rule enforced everywhere.** ECMWF publishes the IFS control and the
   EPS only to 144 h in the 06z/18z runs (verified on the archive), so they are
   off the charts *and* out of the site's model table. 00z/12z keep them.
3. **Two silent landmines fixed:**
   - the runner called `venv/bin/python` (absent there) and wrote to
     `/workspace/data` (absent there) → it could never fetch; the site was only
     as fresh as the last sandbox push;
   - the slim ERA5 cache was read at the **wrong level** (10 hPa asked for the
     100 hPa profile; clim 12.6 instead of 21.5 m/s).
4. **Cycle choice fixed:** newest *complete* cycle (the old "most models" rule
   could pin the site to an older 00z/12z cycle forever).
5. **18z AIFS added**: `output/AIFS_ENS_20261026_1800Z_u10hPa_60N.png` (+ PDF,
   CSV) and the 18z site figures re-rendered.

## Guard rails (so this cannot silently break again)

| what can break | what catches it |
|---|---|
| label shows the day instead of the cycle hour | `scripts/check_site.py` (runs on every deploy, hard gate) |
| climatology read at the wrong level | `scripts/check_site.py` (10 hPa vs 100 hPa sanity) |
| 06z/18z chart starts advertising IFS/EPS | `scripts/check_site.py` + the rule in `pipeline.py` |
| site goes stale (runner dies) | staleness check in `check_site.py` (newest ECMWF cycle vs published) |
| sandbox home wiped → deploy key gone | `make key` (restores from the persistent volume), `make deploy` |
| a session dies mid-task | small commits + this file |

## How to work on it

```bash
make key      # restore the deploy key (after a home reset)
make sync     # fetch + render locally
make check    # health check, local + deployed site
make deploy   # restore key + push -> Pages redeploys
make status   # what the store holds
```

If the deploy key is gone from the persistent volume too, `make key` prints a
fresh public key; add it in **Settings → Deploy keys → Add deploy key** with
*Allow write access* ticked.

## 2026-10-28 01:20Z - the render was crashing silently

Symptom: the site header said `18z 27 Oct 2026` while the chart underneath was
the 12z one, and the domain/level picker did nothing. GEFS was still drawn as a
flat line at 60 m/s.

Cause: `pipeline.render_all()` read `tabS` before it was assigned -> `NameError`
-> every figure of every cycle failed to render. The exception was swallowed, so
the run "succeeded", the health check only *warned* about the missing figures,
and Pages deployed the previous cycle's PNGs under the new cycle's label.

Fixes (commit `0a62ca5`):
- `tabS` is built before it is used, and an empty tab set is skipped.
- each figure render is wrapped: a failure is logged and stored in
  `data/store/render_errors.log` (the store is committed, so it is visible
  without the Actions log - the workflow logs need auth and cannot be read
  from the sandbox).
- the pipeline never publishes a cycle without figures: if the current cycle
  cannot be rendered it re-renders the last published cycle, and if even that
  fails it keeps the published `latest.json` untouched.
- the site's view picker falls back to the newest cycle that has the view, so a
  half-rendered cycle can never leave the picker looking broken.
- the EPS members (~3000 large GRIB files) are only fetched for the newest
  candidate cycle - that fetch was what made the sync take 30+ minutes.

## 2026-10-28 01:55Z - the SSW watch was drawing spaghetti

The user flagged the SSW panel ("should not have these errors"). The data was
fine - no spikes, the member counts matched the lower panel - but the *drawing*
was wrong: one heavy line per member for every easterly stretch. 13 reversed
members x several stretches each = a tangle of broken-looking blue/green lines
below the zero line that read as glitches.

Fix (commit `a9c5788`):
- the members that meet the >= 24 h criterion are now shown as **one dashed
  line per system** - the deepest member that meets the criterion, i.e. how
  deep the reversal gets - instead of one line per member (which was a tangle)
  and instead of a filled envelope (which overlapped the other system and the
  member lines and read as mud past 6 Nov);
- the envelope starts at the real onset of the easterly run, not at the fourth
  step of it (the segment used to start where the criterion became true, which
  is 18 h after the reversal actually began);
- `RENDER_VERSION` - a style change now forces a re-render, so a figure fix
  reaches the site immediately instead of waiting for the next cycle.

## 2026-10-29 00:10Z - what changed while the ERA5 build ran

**Site (published)**
- the domain/level picker is gone: the site shows only the default 60N /
  10 hPa view. The other views (70N, 1 hPa, NH, polar cap) are still
  rendered and kept in the store, just not offered - they need checking
  before they go back on the menu.
- the "Models on this chart" table and its note are gone; the cycle picker
  stays.
- the tab images are now taken strictly from the default view. Before, a
  cycle that lacked it silently showed another view's figure.
- the SSW panel's ERA5 record-min/max box is gone, and the season
  statistics now use Nov -> May (the query windows can wrap the year end).

**Payloads (the hosts are cheaper now)**
- the Pages upload carries only the figures the site serves (the default
  view of every cycle): 21 MB -> 8.3 MB, 52 -> 27 figures.
- the ERA5 climatology the site reads is now a small published artifact
  (per-calendar-day mean/sigma per domain and level, a few hundred KB)
  instead of the raw profiles. The ~850 MB of raw ERA5 and the 27 MB slim
  cache stay off the hosts; the raw cache is still a fallback until the
  artifact lands.

**ERA5**
- the cache is 1991-2020, all year (it was Oct-Dec, and 1959-2022). The
  record statistics stay seasonal: an all-year minimum at 60N/10 hPa is a
  *summer* value (-11 m/s in June, the summer polar easterly), which would
  be a meaningless yardstick for a vortex reversal. An Oct-Dec window would
  have missed the January-February minima, hence Nov-May.
- the store was missing `lat.txt`, which the slim export copies and the
  NH / polar-cap queries need; the build now writes it.
- verified against the data: 60N u@10 hPa runs 32.6 m/s in January, crosses
  zero in April, bottoms near -11 m/s in June, and returns to 30 m/s in
  December. 100 hPa is a different, much weaker profile.

**The sandbox is a burstable pod**
- it gets evicted roughly 30 minutes after the last tool call, which kills
  the ERA5 build and its watchdog. The store survives (it is on the
  persistent volume), so a restart resumes from the manifest - but the
  process does not. Keep a tool call going every ~20 minutes, or restart
  the build when a check-in finds it gone.

## 2026-10-29 02:20Z - the charts were 404-ing for an hour

Symptom: the site header still said `12z 28 Oct` and every chart was a 404.
The published `latest.json` was fine, but no figure was published with it.

Cause (three bugs in a row, all in the same path):
1. The ECMWF publishes the AIFS ENS hours after the initialisation, so for a
   while after a cycle the newest one is missing a model. The pipeline took
   the "waiting for more models" branch and published nothing - and since the
   runner's workspace starts empty, the store had no figures at all.
2. The fallback to re-publish the last good cycle looked up
   `status.json["rendered_cycle"]`, but the status file has always written
   `"rendered"` - the lookup was always `None`, so the fallback never fired.
3. `era5_cache.climatology()` returns pandas Series, not dicts, so the new
   30-day smoother sorted the Series' *values* and raised `KeyError` on a
   float; and the published artifact keyed its values `MM-DD` while the
   charts look them up as `MM-DD-12`.

Fixes: the pipeline now re-renders and re-publishes the last published cycle
whenever the newest one is incomplete (from the series still in the store),
`last_published()` reads the key that exists, the smoother handles Series and
dicts, and the artifact uses the raw cache's key format. All four were
reproduced locally before pushing.

Lesson: reproduce the failure locally before pushing a fix - the runner's
logs need auth and cannot be read from the sandbox, so the local store and a
local pipeline run are the only diagnostic tools that work.

## 2026-10-29 03:10Z - the ERA5 key format was wrong on both sides

While publishing the ERA5 climatology as a small artifact, three
key-format bugs surfaced - all in the same "MM-DD-12" convention:

- `era5_cache.climatology()` built its keys as `M-D-HH` without zero
  padding. They sorted as 10-1, 10-10, 10-2... and the charts, which look
  up `MM-DD-12`, missed every day from the 1st to the 9th of a month.
- the charts built the lookup key the same unpadded way, so the two sides
  only matched by accident, and the new 30-day smoother - which sorts the
  keys - was averaging over a scrambled calendar.
- the artifact's dates came from a synthetic date range instead of the
  climatology's own index, which drifted against it (a leap day, and the
  fact that the index is the union of the calendar days over 30 years).

All three are fixed: the keys are padded at the source, the lookups are
padded, and the artifact's dates come from the index. The health check's
level assertion had the same unpadded lookup and would have failed the
next deploy - it is fixed too.

Verified against the known anchors: 60N/10 hPa = 21.6 m/s on 27 Oct and
29.2 m/s on 15 Nov (the autumn build-up), against 12.8 m/s at 100 hPa.

## 2026-08-29 04:45Z - the picker is back, with the checked views

The domain/level picker is offered again, with three views, all at 10 hPa:
`60N`, the `0-90N` mean and the `65-90N` polar cap. 70N and 1 hPa stay out
of the menu and out of the Pages upload until they have been checked the
same way. `site_meta.PUBLISHED_VIEWS` is the single source of truth - the
menu, the upload list and the tab images all read it.

Accuracy check (AIFS ENS F000 vs the ERA5 1991-2020 climatology for the
same calendar days): 60N +22.1 vs +21.6..+23.3, polar cap +24.2 vs
+20.6..+20.9, NH +5.8 vs +5.4..+6.2 m/s. All three agree with the
climatology.

The polar cap is the striking one: every member reverses (50/50 AIFS and
30/30 GEFS by 03 Nov) where 60N only gets 24/50 - the cap above 65N
breaks down before 60N does.

Also: `data/store/figures` is no longer tracked in the repo. `git add -A
data/store` re-staged the figures every cycle because .gitignore does not
apply to tracked files, and `git rm --cached` refused to remove them
because they had just been re-rendered - it needed `-f`.

## 2026-10-29 06:30Z - the ERA5 archive (the record was there all along)

The u cache was built for the anomalies and the archives, and then it sat
unused while the time-height work chased more ERA5 downloads. It holds the
zonal-mean wind at **all 37 pressure levels for every day from Oct 1959 to
Dec 2020** - the complete 1991-2020 record plus the Oct-Dec of every year
before it. That is a stratospheric archive and it needed no new download.

**What the site gained** (a new "ERA5 archive" tab, `f2884ce`):
- **the reversal catalogue** - every easterly episode of 4 days or more in
  the Nov-May season that ERA5 saw, with its depth, its duration and the
  level it reached. 49 events at 60N, 84 over the polar cap, 29 for the NH
  mean. The criterion matches the SSW watch: westerly to easterly, deeper
  than 5 m/s, at least four days. The record minimum (-30.8 m/s, 2016-03-15)
  reproduces the known anchor exactly.
- **the catalogue figure** - the whole 61-year record on one axis with the
  events marked, and the record minimum called out.
- **time-height cross-sections of the six deepest events** - the zonal wind
  through the stratosphere with the anomaly against the 1991-2020
  climatology in purple contours and the normalized anomaly alongside. The
  2016 event shows easterly flow centred at 1-5 hPa descending to 20-30 hPa
  with westerly flow below - the textbook signature.
- **an interactive browser** - pick a domain and a level, then pan and zoom
  through six decades with the climatology, its +/-2 sigma band and the
  reversal events drawn in. The daily series is one gzipped JSON per domain
  (~1 MB), fetched on demand.

**Where the ERA5 lives** (the question that started this):
- `data/era5_cache/` - the raw ARCO download, **gitignored, build machine
  only**: `profiles.npy` (1.42 GB, float32 `[14000, 37, 721]`),
  `manifest.csv`, `lat.txt`.
- `data/era5_clim_full.json` (3.1 MB) and `data/era5_series.npz` (7.1 MB) -
  derived from it, also gitignored.
- `data/era5_summary.json` (590 KB) - the small artifact the charts read.
- `data/ssw_archive.json` (85 KB) - the catalogue, **committed**.
- `data/store/archive/` (4.9 MB) - the catalogue figure, the event figures
  and the browsable payloads, **committed** (the runner has no ERA5 cache,
  so these are the site's copy).

**Performance work (this is what "optimize" turned up):**
- the ERA5 export is now **one sequential pass** over the store. Reading it
  per level meant 37 random-access passes over 850 MB on a network volume
  and took hours; the streaming pass takes **45 seconds**.
- every rendered figure is quantised to a 256-colour palette - the charts
  have few colours, so this cuts them to about a third with no visible
  difference. The forecast figures went **13.5 MB -> 6.0 MB**.
- the site build **clears its output folder first**: a stale figure from an
  older cycle was being uploaded with the new site (that is where the 23 MB
  preview came from).
- the site serves the PNGs only; the PDF twins stay in the store.

**The z/T downloads are stopped and the partial caches deleted** (2.8 GB).
ARCO has no monthly-mean pressure-level product and no near-real-time
pressure-level store, so z and T would have cost 3.2 h each at the measured
75 MB/s ceiling (the ceiling is bandwidth, not thread count - 12, 32 and 64
threads all give the same 75 MB/s). Nothing the site does needs them: the
u record already carries the wind, the anomalies and the archive. If the
temperature/geopotential panels are wanted later, the ERA5 u cache is not
the place to get them - the model forecast overlay is.


## 2026-10-30 00:45Z - the archive browser was drawing nothing

The archive tab shipped yesterday, but the reversal markers on the
interactive chart were never drawn: **zero** red pixels, on any domain,
at any level. Three bugs stacked:

1. the page passed only the six gallery events, not the 49 in the
   catalogue, and they carried the key `date` while the drawing code
   read `start` - so every one of them was skipped;
2. the event gallery's time-height figures were drawn for the polar cap
   while the catalogue and the captions said 60N - the two disagreed;
3. the date lookup was a linear scan of 13902 entries per event.

The events now travel inside each domain's payload
(`build_archive_payload.py` adds them), so the markers follow the domain
the reader picks: 49 at 60N, 84 over the polar cap, 29 for the NH mean.
The gallery figures use the catalogue's domain. The date lookup is a Map.

Verified in a real browser on the deployed site: the markers draw, the
hover readout gives the date, the value and the anomaly in sigma, and the
level selector works (checked at 1, 10 and 100 hPa). No console errors.

**Lesson:** a canvas that paints *something* is not the same as a canvas
that paints the *right* thing. The first check I wrote ("are there any
non-white pixels?") passed on a blank canvas, because transparent pixels
read as "not white". What found the bug was counting the specific red of
the markers.

**Still not built** (and it is not cheap):
- the *forecast* time-height (the model u/z/T at all levels on top of the
  ERA5): the GFS side is ~27 GB per cycle, and ERA5 z/T is 3.2 h each at
  the measured 75 MB/s ceiling. `scripts/render_timeheight.py` draws it
  but nothing calls it.
- the archive's "recent" window is the **tail of the record** (Sep-Dec
  2020), because the u cache stops there. ARCO's ERA5 stops at 2023-01
  in any case, so the archive cannot reach the present season.

## 2026-10-30 03:50Z - the forecast stratosphere time-height is live

The last piece of the stratosphere work, and the one that looked the most
expensive. It was not.

**The cost.** The earlier estimate of ~27 GB per cycle was wrong. The
NOAA `.idx` files give the byte range of every GRIB message, so only the
messages we want are downloaded. Measured on the real thing: a 16-day,
15-level time-height is **0.6 GB and 95 seconds** (`scripts/fetch_gfs_timeheight.py`).

**What it shows.** The GFS zonal-mean wind at 15 levels across the
forecast, against the ERA5 1991-2020 climatology for the same calendar
days, as a pressure x time cross-section with the anomaly in purple
contours. On the 29 Oct 18z run over the polar cap: the westerly vortex
gives way to a deep easterly blob that starts at 1-3 hPa and descends to
20-30 hPa by 6 Nov, with a -20 m/s anomaly. The textbook SSW signature,
descending. At 60N the same run only weakens the vortex - it does not
reverse it - which is why the tab shows the polar cap.

**Three things that had to be fixed:**
- the runner had no `data/era5_clim_full.json` (the full-resolution
  climatology is a build-machine artifact). The slice the figure needs is
  now committed: `data/era5_clim_th.json`, 15 levels x 4 domains, 969 KB.
- the height scale on the right was a twin of the left panel, and
  sharing the y made it overwrite the pressure labels. It is its own
  axes now.
- the title showed the raw cycle id; it uses the house-style label now.

**Two traps worth remembering:**
- a stale `__pycache__` .pyc shadowed an edit on the NAS volume - the
  filesystem's timestamps fooled Python's cache check, and the new code
  simply did not run. `find . -name __pycache__ -exec rm -rf {} +` when a
  change seems to have no effect.
- `sharey=True` between a pressure axis and a height axis makes the two
  fight over the labels. Do not share.

## 2026-10-30 06:05Z - the stratosphere is two products now, in a new colour scheme

The combined wind+anomaly cross-section was doing two jobs badly: the
brown/teal `BrBG` ramp was hard to read, and the wind field and the
anomaly are different products that get consulted separately.

**Two graphics, not one.** `scripts/render_forecast_timeheight.py` now
renders two files per cycle and per domain:

    stratosphere_<cycle>_u10_<domain>.png    the zonal-mean wind (m/s)
    anomaly_<cycle>_u10_<domain>.png         the anomaly vs ERA5 (m/s)

The second one is a **new tab** ("Stratosphere anomaly"), pinned to the
polar cap like the first (`site_meta.TAB_VIEW`). The ERA5 archive events
were split the same way: `event_<date>.png` and `eventanom_<date>.png`,
and each gallery card carries both.

**The colour scheme** is now the standard anomaly convention, in every
stratosphere graphic: `RdBu_r` - **blue is negative** (easterly wind, or
weaker than normal) and **red is positive** (westerly, or stronger). So
the reversal reads as blue against the red vortex. The 0 m/s line is
drawn as a **dashed** contour (the edge of the vortex), not a line on the
pressure axis - and the ERA5 climatology stays black and solid.

**The anomaly is in m/s, not sigma.** The normalised anomaly reached -9
sigma on the 29 Oct run (a -41 m/s departure against a 4.6 m/s
interannual spread), which is a number nobody can read. m/s is also what
the wind product is in, so the two cross-sections can be read against
each other.

**Three bugs found on the way:**
- the height axis on the right had **no numbers at all**: the level
  filter was `ylo <= p <= yhi` with ylo=300 (hPa, the bottom) and
  yhi=1 (the top), so it was always empty. The pressure axis was saved
  by the default log locator, which is why only the height scale looked
  broken.
- `contourf(levels=18, norm=...)` **ignores the norm**: the levels come
  from the data, so the colour bar showed the raw data range instead of
  the symmetric one we asked for. The levels have to be given
  explicitly (`np.linspace(vmin, vmax, 21)`).
- the colour bar's tick labels collided with the height scale (they are
  drawn to the right of the bar) - the gap between the two axes is now
  0.058 of the figure width.

**One matplotlib trap:** `linestyles=(0, (5, 2))` raises "Do not know how
to convert [0] to dashes". Use the string `"dashed"`, or `linestyle="--"`
for a legend handle.

## 2026-10-30 06:25Z - the next request: zonal HEIGHT anomalies, 1990-2020 (paused)

The archive tab is to lose **two things** - the "ERA5 stratosphere archive"
catalogue figure and the "Browse the record" interactive canvas - and become
instead:

> a time series of **zonal geopotential-height anomalies**, **1-1000 mb**,
> **60-90N**, **ERA5**, **1990-2020, all year**.

Height, not wind: a polar vortex is a deep cold column, and the classic
measure of a sudden stratospheric warming is the height ANOMALY at 10 hPa
over the polar cap (the lower the height, the warmer and weaker the vortex).
The natural figure is the height-anomaly time-height cross-section (x = time
1990-2020, y = pressure 1-1000 hPa, colour = the anomaly against the
1991-2020 climatology for the same calendar day).

**The blocker is the data: ERA5 `geopotential` is not cached.**
- `scripts/era5_height_cache.py` is written and ready: it fetches the 12Z
  step of every day in 1990-2020 from ARCO, keeps the zonal means for
  60-90N, 60N and 0-90N on all 37 levels, and is **resumable** (a manifest
  row per finished day) so it can run in the background.
- **Cost: ~1.3 TB of transfer, ~5 h at the measured 75 MB/s ceiling.**
  The ARCO pressure-level chunks hold one 6-hourly step of the whole
  0.25 deg globe (37 x 721 x 1440, ~120 MB compressed), so one day costs one
  chunk and the latitude band cannot be subset.
- **There is no cheap substitute.** The coarse ARCO stores
  (`ar/1959-2022-6h-{64x32,128x64,512x256}_equiangular_conservative.zarr`)
  look promising but are **single-level** (13 pressure levels, 250 hPa and
  below) - no stratosphere. Only `ar/*full_37*` has the 37 levels, and they
  are all 0.25 deg. The 6-hourly and the hourly stores have the same
  chunk shape, so the hourly one is no smaller.
- **No CDS API key** is configured, so the CDS route (which has monthly
  means and a proper pressure-level product) is not available.

**To run it** (resumable - kill it and start it again and it continues):

    nohup ./venv/bin/python -u scripts/era5_height_cache.py \
        --years 1990-2020 --workers 4 > /workspace/scratch/height.log 2>&1 &

then, with the cache built:

    ./venv/bin/python scripts/build_height_archive.py     # the figure + the
    ./venv/bin/python scripts/build_site.py                # the site

**Also still open:** the runner had been building the site from `db15d46`
for 40+ minutes at the time of writing, so the live site still shows the
old combined stratosphere graphic. The two new products (the wind and the
anomaly, as separate tabs) are in that commit and will appear when it lands.

## 2026-10-31 05:55Z - the site is down (not published)

The charts are no longer served publicly. `https://massachusettswx-weather.github.io/Forecast-charts/`
now returns a one-line notice (`webapp/static/offline.html`), and every
figure, the archive payloads and `latest.json` are 404 again.

**How it works now:**
- `update.yml` still builds the charts every cycle and still keeps them in
  the repository. Before deploying, the **"The public page"** step replaces
  `site_out` with the notice **unless** the repository variable
  `PUBLISH_SITE` is `"true"`. So the charts are never published by
  accident, and the takedown survives every poll.
- `.github/workflows/unpublish.yml` is the immediate lever: it publishes the
  notice page without the fetch and the render, in about a minute. Touch
  that file and push to fire it.
- The chart workflow's push trigger now lists `.github/workflows/update.yml`
  only, so adding the takedown workflow does not queue another full run.

**To bring the site back:** set the repository variable `PUBLISH_SITE=true`
(Settings -> Secrets and variables -> Actions -> Variables). The next cycle
publishes the charts again. Nothing has to be rebuilt or re-fetched - the
chart pipeline never stopped.

**Still public (the repository, not the site):** the source, `latest.json`
and the committed archive PNGs are readable on raw.githubusercontent.com. That
only goes away if the repository itself is made private (Settings ->
General -> Danger zone -> Change visibility). The deploy key and the
Actions workflow keep working on a private repository.

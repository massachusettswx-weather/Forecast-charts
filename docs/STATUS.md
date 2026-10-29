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

## Next things worth doing

- Archive browser for older cycles.
- Slack/webhook when a new cycle is published.
- More models (METEO-FRANCE, UKMO, GenCast).
- ERA5 long record (1959-2022) is building in the background - when it lands,
  export a small summary artifact (monthly mean/sigma + record minimum) so the
  runner can use it.

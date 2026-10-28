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
- the members that meet the >= 24 h criterion are now shown as **one shaded
  envelope** (how deep the reversal goes) with a legend entry, instead of one
  line per member;
- the envelope starts at the real onset of the easterly run, not at the fourth
  step of it (the segment used to start where the criterion became true, which
  is 18 h after the reversal actually began);
- `RENDER_VERSION` - a style change now forces a re-render, so a figure fix
  reaches the site immediately instead of waiting for the next cycle.

## Next things worth doing

- Archive browser for older cycles.
- Slack/webhook when a new cycle is published.
- More models (METEO-FRANCE, UKMO, GenCast).
- ERA5 long record (1959-2022) is building in the background - when it lands,
  export a small summary artifact (monthly mean/sigma + record minimum) so the
  runner can use it.

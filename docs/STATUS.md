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

## Next things worth doing

- Level/domain selector on the site (AIFS stops at 10 hPa, IFS/EPS/GEFS reach 1 hPa).
- Archive browser for older cycles.
- Slack/webhook when a new cycle is published.
- More models (METEO-FRANCE, UKMO, GenCast).

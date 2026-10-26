# Design: automatic stratosphere forecast site

Goal: a website that keeps itself current — when a new cycle is published by
ECMWF or NCEP, the charts for the affected models are regenerated and go live
without anyone touching a button.

## What already exists (reusable as-is)

| piece | status |
|---|---|
| ERA5 climatology cache (1991-2020, 1 Oct-31 Dec, **all 37 levels**) | built, 295 MB, level-agnostic, instant queries |
| ECMWF AIFS ENS / AIFS Single / ECMWF EPS + IFS control fetchers | working |
| NOAA GEFS mean+control, GFS deterministic fetcher | working |
| Figure builders in the house style | working (`make_figure.py`, `make_multimodel.py`) |
| Range-fetch trick (fetch only the u@LEVEL message via published `.index`) | working, ~0.5 MB per member/step |

## 1. Run store (the source of truth)

A flat, content-addressed store on disk. No database required, but a small
SQLite index is convenient for "what's the latest cycle per model".

```
data/store/
  index.sqlite                  # rows: model, cycle, level, domain, status, times, paths
  series/<model>_<cycle>_<level>_<domain>.npz   # control/mean/member profiles
  figures/<model>_<cycle>_<level>_<domain>.png  # + .pdf
  latest.json                   # {model: {cycle, level, domain, figure, series}}
```

Key = (model, cycle, level, domain). Same key = same result, so re-runs are
idempotent and the site never needs to re-render anything it already has.

## 2. Pipeline (one command per model-cycle)

```
scripts/pipeline.py sync --model aifs-ens      # resolve newest cycle, fetch, compute, render
scripts/pipeline.py run  --model aifs-ens --cycle 202610261200 --level 10 --lat 60
```

`sync` resolves "the newest published cycle" per model by listing the archive:
- ECMWF: `GET /forecasts/` -> newest date dir -> newest `NNz` subdir (a 12z run
  appears ~6 h after init; a cycle can be partially written, so verify the last
  step exists before rendering).
- NCEP: list the S3 prefix `gfs.<date>/` / `gefs.<date>/`.

States per job: `pending -> fetching -> computed -> rendered -> failed` (with the
error stored), so a partial outage never breaks the site — the last good
figures simply stay up.

## 3. Auto-update (the scheduler)

- A cron entry (or systemd timer / Cloud Scheduler / GitHub Action) every
  10-15 min: `python3 scripts/pipeline.py sync --all`.
- The site serves `latest.json`; a new cycle only changes what the tab shows.
- Optional: webhook to Slack/Discord + email when a new cycle is live, and an
  "age" badge (e.g. "12z run, published 6 h ago") so staleness is visible.

## 4. Site

Recommended: **FastAPI + Jinja2 templates + a small JSON API**, serving the PNGs
straight off disk.

- `GET /`                     -> tabs: Physics | AI | Hybrid | Ensembles
- `GET /api/latest`           -> latest.json (what the front end renders)
- `GET /api/series?model=..`  -> raw series for the interactive charts
- `GET /fig/<path>.png`       -> static figure files

Why not a static site: content changes 4x/day; a rebuild step adds friction.
The same store can still be published as a fully static site (one `sync` +
one `generate`) if serverless hosting is preferred.

Front end: one small JS bundle (or plain server-rendered HTML) with a
shared chart component; level selector (1/3/10/30/100/250 hPa — all already
cached), latitude selector (60N / NH mean), and cycle picker.

## 5. Tabs and model taxonomy

**Physics-based**
- ECMWF IFS (deterministic) — `ifs/0p25/oper`
- ECMWF EPS (ensemble, 50 perturbed + control) — `ifs/0p25/enfo/*-enfo-ef`
- NOAA GFS (deterministic) — `noaa-gfs-bdp-pds`
- NOAA GEFS (ensemble mean + control) — `noaa-gefs-pds`

**AI**
- ECMWF AIFS Single (deterministic) — `aifs-single/0p25/oper`
- ECMWF AIFS ENS (51 members) — `aifs-ens/0p25/enfo`

**Hybrid** — needs a decision (see open questions): either
(a) *paired* systems: one card per centre showing its physics run beside its AI
run (ECMWF IFS vs AIFS, NOAA GFS vs GEFS) so the engine difference is the
point of the tab; or
(b) models that genuinely mix physics and AI inside one forecast chain
(GraphDOP-style NWP-ML hybrids, neural-physics hybrids) — none of which are
currently reachable from the public archives used here.

**Ensembles**
- AIFS ENS, ECMWF EPS, NOAA GEFS drawn as member spaghetti (thin blue) with a
  bold mean and bold control, exactly as done for the AIFS ENS chart. Requires
  the fetchers to keep per-member profiles (AIFS ENS already does; EPS/GEFS
  currently keep only the mean — a small change).

## 6. Roadmap

1. **Run store + pipeline** (~1-2 h): store layout, `pipeline.py`, populate it
   with the runs already fetched, `latest.json`. CLI: `pipeline.py list`.
2. **Site v1** (~1-2 h): FastAPI app, tab structure, model cards, figures from
   the store, level/latitude selectors.
3. **Auto-update** (~30-60 min): `sync --all` + cron, plus a "published
   <age>" badge and optional webhook.
4. **Ensemble tab** (~30 min): keep member profiles for EPS/GEFS, spaghetti
   renderer, spread statistics.
5. **Scale out** (ongoing): more models (METEO-FRANCE, UKMO, JMA, Google
   GenCast / Aurora if their feeds become accessible), archive browser,
   30-year reforecast comparisons, and the existing ERA5 cache extended to
   other months (it currently covers 1 Oct - 31 Dec).

## 7. Open questions

1. **Hosting**: this sandbox is a 3 GB container with no cron/systemd. Target a
   small VPS (systemd timer + nginx), a Cloud Run job + CDN, or GitHub Actions +
   Pages?
2. **Access**: public site, or behind auth / on the internal network?
3. **"Hybrid"**: paired physics-vs-AI per centre (option a), or genuinely
   hybrid models (option b)?
4. **Model list** for phase 1: ECMWF + NOAA only, or add METEO-FRANCE / UKMO /
   JMA / GenCast?
5. **Figure family**: 60N zonal-mean only, or also NH mean and latitude
   profiles (the ERA5 cache supports every latitude instantly)?

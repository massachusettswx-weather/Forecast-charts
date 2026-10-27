# The Stratosphere Weather page

One page covering stratospheric weather, built from one backbone: a
**climatology layer** (reference climatology for any variable/level/domain/year
range) + a **run store** (raw archive → normalised series → figures) + the
**site** (FastAPI + static Pages build, 4×/day).

## Sections of the page

| # | section | reference image | data needed | state |
|---|---|---|---|---|
| 1 | **Now** — 60N zonal-mean zonal wind at 10 hPa, every system on one graph, members + mean + control, ERA5 climatology | pic 2 | ECMWF AIFS ENS/Single, ECMWF EPS/IFS, NOAA GFS/GEFS | ✅ live |
| 2 | **Members** — all ensembles' member spaghetti side by side | pic 2 | same | ✅ live |
| 3 | **SSW watch** — member-based probability that 60N/10 hPa zonal wind goes easterly (<0 m/s) | pic 5 | member series (in hand ✅) | ⬜ |
| 4 | **Time–height anomalies** — normalised GPH / polar-cap temperature anomaly cross-section, ERA5 analysis + forecast | pics 1 & 6 | ERA5 `geopotential`, `temperature` (ARCO ✅), NCEP CORE for 1948– | ⬜ |
| 5 | **Climatology reference** — mean, percentile bands, record max/min, any period | pic 3 | ERA5 + NCEP REANALYSIS (CORE 1948–) | 🔨 partial |
| 6 | **Extended / seasonal outlook** — 45-day & monthly members with long climatology | pics 3 & 4 | GEFS-Ext, ECMWF-Ext, SEAS5, CFS | ⬜ access blockers |

## Backbone

Everything sits on one backbone: a **climatology layer** (reference climatology for any
variable/level/domain/year range) + a **run store** (raw archive → normalised series → figures)
+ the **site** (FastAPI + static Pages build, 4×/day).

Status legend: ✅ built · 🔨 in progress · ⬜ not started

---

## 0. Backbone (✅ built, 🔨 extending)

| piece | state |
|---|---|
| ERA5 climatology cache (1991–2020, 1 Oct–31 Dec, 37 levels, daily 12Z) | ✅ 2,760 steps, 295 MB, level-agnostic |
| Run store + pipeline (`scripts/pipeline.py`) | ✅ idempotent, locked, 3 s on a warm cache |
| Fetchers: AIFS ENS / AIFS Single / ECMWF EPS + IFS ctl / GFS / GEFS mean+ctl+members | ✅ |
| 4-tab site + GitHub Pages watcher (02/08/14/20 UTC) | ✅ live |
| **Climatology layer: custom years, more variables, daily 6-hourly** | 🔨 next |

### Verified data access (probed, not assumed)

| dataset | access | notes |
|---|---|---|
| ERA5 (ARCO, native 0.25°) | `gs://gcp-public-data-arco-era5/ar/1959-2022-full_37-6h-0p25deg-chunk-1.zarr-v2` | has `geopotential`, `temperature`, `u/v_component_of_wind`, `vertical_velocity` ✅ — everything needed for pics 1 & 6. **PV is NOT on pressure levels** (model-level only) → compute PV or use a 1-PVU proxy. |
| ERA5 cost | 117 MB **per 6-hourly step** (all 37 levels in one chunk) | a full 1991–2020 *daily 12Z* climatology = 10,950 steps ≈ **1.3 TB ≈ 5 h one-off** (cached forever after). The 1.4° "conservative" product is ~100× cheaper but tops out at 50 hPa — **no 10 hPa** → use native for the stratosphere, coarse for the troposphere. |
| NCEP/NCAR Reanalysis (1948–) | `psl.noaa.gov/thredds/dodsC/Datasets/ncep.reanalysis.derived/` | OPeNDAP ✅, levels 1000→**10 hPa** ✅. **Precomputed 1991–2020 monthly LTM files** (`uwnd.mon.ltm.1991-2020.nc`) → climatology for free. Per-year daily files → the cheap 1948–present backbone. **Note: NCEP/NCAR R1 stopped production 17 Mar 2026** (use NCEP/DOE R2 from 1979 for the modern part). |
| GEFS (0–16 d) | `s3://noaa-gefs-pds/gefs.<date>/<cyc>/atmos/pgrb2ap5/` ✅ | `geavg` mean ✅, `gec00` control ✅, `gep01–30` ✅ (already wired) |
| GEFS-Extended (45 d) | ⬜ | not in the NODD bucket; NCEP archive/NOMADS only |
| ECMWF open data (IFS/AIFS) | `data.ecmwf.int` ✅ | rolling **12 runs only** (~2–3 days) — no history |
| **ECMWF-Extended (46 d) / SEAS5** | ⬜ **not in the open-data portal** (404 for `ext`/`seas`/`med`) | needs the **CDS/MARS** route → the hardest data-access item in the plan |
| CFSv2 (seasonal) | ⬜ | NCEP archive / NOMADS (NOMADS is **403 from this sandbox**) |

---

## 1. ERA5 climatology, all years / custom years  🔨
*Parameterise the cache:* `--years 1991-2020` (or any range), `--vars u,v,T,Z`, `--daily/--6hourly`,
`--domain 60N / NH / polar-cap 65-90N`. Store per-variable zonal-mean profiles + polar-cap
means, so **any** latitude/aggregate is a local lookup.
*Cost:* 1.3 TB / ~5 h for a daily 1991–2020 build (one-off). Mitigate: build 6-hourly for the
SSW season only (Oct–Mar) and daily for the rest; use PSL's precomputed LTM for the coarse check.
*Also needed:* a **1948–present** backbone → NCEP Reanalysis from PSL (cheap per-level files) ✅.

## 2. Time-series options (pic 1)  ⬜
**Zonal normalised geopotential-height anomaly**: time × pressure cross-section, ERA5 past
~60 days + forecast forward, anomalies vs 1991–2020, colour scale ±σ.
*Needs:* ERA5 `geopotential` at all 37 levels (same 117 MB/step cost ⚠) + a zonal-normalisation
step (divide by the climatological zonal-mean Z at each level/date).
*Also:* the same cross-section for model forecasts (ERA5 reanalysis vs GFS forecast overlay).

## 3. Every ensemble system on one graph (pic 2)  ✅ done
AIFS ENS (50) + ECMWF EPS (50) + GEFS (30) members with bold mean/control ✅ (site → Ensembles tab).
*Next:* add **CFS/GEFS-Ext/ECMWF-Ext** members as they become available; cross-system member spread stats.

## 4. Seasonal / extended infrastructure  ⬜
GEFS-Ext (45 d), ECMWF-Ext (46 d), SEAS5, CFS → *members* plotted against a long climatology
(1958–2025 for the 60N 10 hPa wind, like pic 3) with percentile bands and record max/min.
*Blockers:* ECMWF-Ext/SEAS5 need CDS/MARS; CFS needs the NCEP archive; NOMADS is 403 here.
*Plan:* build the **runner abstraction** first (one `Model` interface: cycle → members → series),
so each new system is a drop-in.

## 5. SSW probability graph (pic 5)  ⬜
Member-level `u@10 hPa, 60N` → for each system: the ensemble mean ± p10/p90 band, the
"80 % of members" envelope, the dashed normal range, and a bottom panel of
**% of members below 0 m/s** (reversal probability) per day.
*We already have the raw material* — AIFS/EPS/GEFS member series at 60N are cached ✅;
this is plotting + a probability product. *Cheapest high-value item on the list.*

## 6. Polar-cap temp/height anomalies + PV archive (pics 1 & 6)  ⬜
60–90°N (or 65–90°N) temperature/height anomaly cross-sections, anomalies vs 1991–2020, with
**NOAA CORE (1948–)** for the pre-ERA5 era and ERA5 after → a 78-year archive ✅.
*PV:* not available on ERA5 pressure levels → compute from model-level u/v/T, or track a
1-PVU isentropic proxy (cheaper, adequate for a wind-reversal diagnostic).
*Also:* El Niño analog composites (pic 6) — needs SST (ERSST/NOAA OI SST ✅).

---

## Suggested order
1. **Climatology layer** (custom years + variables + domains) — everything else depends on it.
2. **SSW probability** (pic 5) — data already in hand, pure product work.
3. **Polar-cap temp/height cross-sections** (pics 1 & 6) — needs the layer + CORE.
4. **Seasonal systems** (pics 3/4) — needs CDS/MARS/NCEP-archive access; biggest data hurdle.
5. **Long-range/GEFS-Ext** — same runner, same plumbing.

## Architecture change implied
Generalise the store from "figures for one product" to:
```
data/store/series/<model>_<cycle>_<var>_<level>_<domain>.npz
data/store/products/<product>_<cycle>.png      # each pic = one product
data/store/latest.json                        # what the site serves
```
with `climatology` as a **provider** (ERA5-ARCO | NCEP-REANALYSIS | NCEP-CORE),
selected by variable/level/domain/year-range.

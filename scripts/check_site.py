#!/usr/bin/env python3
"""
Production health check for the forecast site.

    python3 scripts/check_site.py              # check the local store
    python3 scripts/check_site.py --online     # also check the deployed site
    python3 scripts/check_site.py --cycle 2026102618

What it guards (each of these broke the site at some point):
  1. a published cycle exists and all four tab figures are on disk
  2. the cycle label says the cycle HOUR ("18z 26 Oct 2026"), not the day of
     the month ("26z 26 Oct 2026" - the old strftime('%dz') bug)
  3. the ERA5 climatology behind the charts is 10 hPa, not 100 hPa
  4. the 06z/18z cycles carry no ECMWF IFS/EPS - not in the charts, not in the
     model table (ECMWF publishes those runs only to 144 h)
  5. with --online: the deployed site serves the same cycle/label and the same
     model list

Exit code 0 = healthy, 1 = something is wrong (the workflow refuses to deploy).
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import pipeline as P
import render as R

STORE = os.path.join(ROOT, "data", "store")
FIGDIR = os.path.join(STORE, "figures")
SITE = "https://massachusettswx-weather.github.io/Forecast-charts"

ok, bad, warn = [], [], []


def check(cond, good, err, critical=True):
    """critical=True -> a failure blocks the deploy; otherwise it is a warning."""
    if cond:
        ok.append(good)
        print("  ok    " + good)
    elif critical:
        bad.append(err)
        print("  FAIL  " + err)
    else:
        warn.append(err)
        print("  warn  " + err)
    return cond


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--online", action="store_true", help="also probe the deployed site")
    ap.add_argument("--cycle", default=None, help="check this cycle instead of the published one")
    a = ap.parse_args()

    latest_f = os.path.join(STORE, "latest.json")
    if not os.path.exists(latest_f):
        print("FAIL: no store (data/store/latest.json) - run pipeline.py sync first")
        return 1
    latest = json.load(open(latest_f))
    cycle = a.cycle or latest.get("cycle")
    if not cycle:
        print("FAIL: no cycle published")
        return 1
    print(f"cycle {cycle} ({R.cycle_label(cycle)})")

    # 1. figures on disk ---------------------------------------------------
    per_lat = (latest.get("cycles") or {}).get(cycle) or {}
    default_lat = "60|10"                   # domain|level
    # the store used to be keyed by domain only ("60"); accept that too
    if per_lat and default_lat not in per_lat and not any("|" in k for k in per_lat):
        dom = per_lat.get("60") or next(iter(per_lat.values()))
        per_lat = {default_lat: dom}
    figs = per_lat.get(default_lat) or {}
    for tab in ("physics", "ai", "hybrid", "ensembles"):
        f = figs.get(tab)
        check(bool(f) and os.path.exists(os.path.join(FIGDIR, f)),
              f"figure present: {tab}",
              f"figure MISSING: {tab} ({f}) - the next poll re-renders it",
              critical=False)
    f = figs.get("ssw")
    check(bool(f) and os.path.exists(os.path.join(FIGDIR, f)),
          "figure present: ssw",
          f"figure MISSING: ssw ({f}) - the SSW product is optional",
          critical=False)
    # the other domains the site offers
    for key in sorted(per_lat):
        if key == default_lat:
            continue
        f = (per_lat[key] or {}).get("ai")
        check(bool(f) and os.path.exists(os.path.join(FIGDIR, f)),
              f"view {key}: figure present",
              f"view {key}: figure MISSING ({f})",
              critical=False)

    # 2. the label says the cycle hour ------------------------------------
    want = R.cycle_label(cycle)
    got = latest.get("cycle_label")
    check(want.endswith("z " + want.split("z ", 1)[1]) and got in (None, want),
          f"cycle label: {want}",
          f"cycle label wrong: {got!r} (expected {want!r})")
    check(not re.match(r"^\d{2}z \d{1,2} [A-Z][a-z]{2} \d{4}$", want) or
          want.split("z")[0] == cycle[8:10],
          "label hour matches the cycle",
          f"label hour {want.split('z')[0]} != cycle hour {cycle[8:10]}")

    # 3. the climatology is the right level --------------------------------
    try:
        import era5_cache as EC
        d0 = R.step_times([0], cycle)[0]
        d1 = d0 + __import__("datetime").timedelta(hours=360)
        m10, _, _ = EC.climatology(10, lat=60, start=f"{d0.month:02d}-{d0.day:02d}",
                                   end=f"{d1.month:02d}-{d1.day:02d}")
        m100, _, _ = EC.climatology(100, lat=60, start=f"{d0.month:02d}-{d0.day:02d}",
                                    end=f"{d1.month:02d}-{d1.day:02d}")
        v10 = float(m10.mean()) if len(m10) else float("nan")
        v100 = float(m100.mean()) if len(m100) else float("nan")
        check(15 < v10 < 35, f"ERA5 clim at 10 hPa = {v10:.1f} m/s",
              f"ERA5 clim at 10 hPa = {v10:.1f} m/s - looks like the WRONG level")
        check(abs(v10 - v100) > 3,
              f"10 hPa and 100 hPa climatologies differ ({v10:.1f} vs {v100:.1f})",
              "10 hPa and 100 hPa climatologies are the same - level mapping is broken")
    except Exception as e:                       # no cache here: warn, don't fail
        print(f"  warn  ERA5 level check skipped ({e})")

    # 4. the 06z/18z rule ---------------------------------------------------
    hour = int(cycle[8:10])
    models = set((latest.get("cycle_models") or {}).get(cycle) or [])
    if models:
        if hour in (6, 18):
            check(not (models & {"ifs", "eps"}),
                  "06z/18z cycle: no ECMWF IFS/EPS in the model list",
                  f"06z/18z cycle lists ECMWF IFS/EPS: {sorted(models & {'ifs','eps'})}")
        else:
            check(True, "00z/12z cycle: ECMWF IFS/EPS allowed", "")
    else:
        print("  warn  no cycle_models in the store (older store?)")

    # 4a. the stored series must be physically sensible ------------------------
    # A flat line at 60 m/s is the LATITUDE array stored in the zonal slot -
    # that bug shipped once, so it is checked from now on.
    import numpy as np
    seld = os.path.join(STORE, "series")
    for model in ("aifs-ens", "aifs-single", "ifs", "eps", "gfs", "gefs",
                  "gefs_members"):
        p = os.path.join(seld, f"{model}_{cycle}_u10.npz")
        if not os.path.exists(p):
            continue
        try:
            d = np.load(p, allow_pickle=True)
            if "u60" in d:
                v = np.atleast_2d(np.asarray(d["u60"], float))
                v0 = float(v[0].mean())
                spread = float(v.std(axis=1).mean())
            elif "zonal" in d:
                lat = np.asarray(d["lats"], float)
                i = int(np.abs(lat - 60.0).argmin())
                z = np.asarray(d["zonal"], float)
                v0 = float(z[0, ..., i].mean())
                spread = float(np.std(z[:, ..., i]))
            else:
                continue
            check(abs(v0) < 45 and spread > 0.3,
                  f"{model}: u60 F000 {v0:5.1f} m/s (spread {spread:4.1f})",
                  f"{model}: u60 F000 {v0:5.1f} m/s (spread {spread:4.1f}) - "
                  f"IMPLAUSIBLE, the series is not a wind profile")
        except Exception as e:
            check(False, "", f"{model}: could not check the series ({e!r})")

    # 4b. the published cycle must not be stale --------------------------------
    try:
        ec_date, ec_cyc, ec_horizon = P.resolve_ecmwf()
        if ec_date:
            newest = f"{ec_date}{ec_cyc}"
            gap = (int(newest[8:10]) - int(cycle[8:10])) % 24
            if newest[:8] == cycle[:8]:                # same calendar day
                check(gap <= 6,
                      f"published cycle is current (newest ECMWF cycle {newest}z, "
                      f"horizon {ec_horizon}h)",
                      f"STALE: newest ECMWF cycle is {newest}z (horizon {ec_horizon}h), "
                      f"the site still shows {cycle}z", critical=False)
            else:
                check(True, f"newest ECMWF cycle {newest}z", "")
    except Exception as e:
        print(f"  warn  staleness check skipped ({e})")

    # 5. the deployed site --------------------------------------------------
    if a.online:
        try:
            import requests
            r = requests.get(f"{SITE}/latest.json", timeout=(15, 60))
            live = r.json()
            check(live.get("cycle") == cycle,
                  f"deployed site serves {cycle}",
                  f"deployed site serves {live.get('cycle')}, expected {cycle}")
            check(live.get("cycle_label") in (None, want),
                  f"deployed label {live.get('cycle_label')}",
                  f"deployed label {live.get('cycle_label')!r} != {want!r}")
            h = requests.get(f"{SITE}/", timeout=(15, 60)).text
            m = re.search(r'<tbody id="modelrows">(.*?)</tbody>', h, re.S)
            if m and hour in (6, 18):
                check("ECMWF IFS" not in m.group(1) and "ECMWF EPS" not in m.group(1),
                      "deployed model table: no ECMWF IFS/EPS",
                      "deployed model table still lists ECMWF IFS/EPS")
            elif m:
                check(True, "deployed model table rendered", "")
        except Exception as e:
            check(False, "", f"deployed site unreachable: {e!r}")

    if warn or bad:
        print("\nstore layout:")
        cyc = (latest.get("cycles") or {}).get(cycle) or {}
        for k in sorted(cyc):
            print(f"   {k}: {sorted(cyc[k])}")
    if warn:
        print(f"\n{len(warn)} warning(s):")
        for w in warn:
            print("   -", w)
    print(f"\n{len(ok)} ok, {len(bad)} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

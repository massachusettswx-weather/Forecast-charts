#!/usr/bin/env python3
"""
The ERA5 SSW archive.

The u cache holds the ERA5 zonal-mean wind at all 37 levels for every day
from Oct 1959 to Dec 2020 - a 61-year record of the polar stratosphere. That
is the archive: every sudden stratospheric warming that ERA5 saw, with the
depth of the reversal and the level at which it happened.

This script finds the reversal events in the record, the same way the SSW
watch finds them in a forecast ensemble:

    easterly (< 0 m/s) for 24 h or more = 4 consecutive 6-hourly steps

The ERA5 record is daily (12Z), so a reversal is 4 consecutive DAILY steps
here - that is 4 days of easterly flow, a stronger requirement than the
24 h the ensemble uses. Every event is reported with the date it started,
how deep it got, how long it lasted and which level carried it.

    python3 scripts/ssw_archive.py --out data/ssw_archive.json
"""
import argparse
import csv
import json
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.environ.get("ERA5_CACHE_DIR", os.path.join(ROOT, "data", "era5_cache"))
SERIES = os.path.join(ROOT, "data", "era5_series.npz")

LEV37 = [1, 2, 3, 5, 7, 10, 20, 30, 50, 70, 100, 125, 150, 175, 200, 225,
         250, 300, 350, 400, 450, 500, 550, 600, 650, 700, 750, 775, 800,
         825, 850, 875, 900, 925, 950, 975, 1000]
DOMAINS = ["60", "cap", "0"]


def _runs(mask):
    """The index ranges of consecutive True values."""
    out = []
    start = None
    for i, m in enumerate(mask):
        if m and start is None:
            start = i
        elif not m and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(mask)))
    return out


def events_for(series, dates, level_idx, min_days=4, min_depth=0.0,
               season=(11, 5)):
    """The reversal events in one series.

    Only the SSW season counts: the polar vortex is westerly in summer, so
    an unrestricted search finds "reversals" every June.

    A run also has to be a real REVERSAL: the wind must be westerly before
    it, and it must reach a given depth. A marginal excursion to -1 m/s in a
    westerly vortex is not a stratospheric warming.
    """
    vals = series[level_idx]
    ok = ~np.isnan(vals)
    month = np.array([int(d[5:7]) for d in dates])
    lo, hi = season
    if lo <= hi:
        in_season = (month >= lo) & (month <= hi)
    else:                                  # the season wraps the year end
        in_season = (month >= lo) | (month <= hi)
    # a reversal needs consecutive CALENDAR days, so a gap in the record
    # breaks the run
    day = np.array([int(d[8:10]) for d in dates])
    contig = np.zeros(len(vals), dtype=bool)
    for i in range(1, len(vals)):
        contig[i] = ok[i] and ok[i - 1] and (day[i] == day[i - 1] + 1)
    mask = ok & in_season & (vals < 0)
    # split the runs at any gap
    evs = []
    for a, b in _runs(mask):
        seg = list(range(a, b))
        cur = []
        for i in seg:
            if cur and not contig[i]:
                if len(cur) >= min_days:
                    evs.append(cur)
                cur = []
            cur.append(i)
        if len(cur) >= min_days:
            evs.append(cur)
    out = []
    for r in evs:
        v = vals[r]
        j = int(np.argmin(v))
        # the reversal must be a reversal: westerly before it
        pre = vals[max(0, r[0] - 10):r[0]]
        pre = pre[~np.isnan(pre)]
        was_westerly = bool(len(pre) == 0 or pre.max() > 0)
        out.append({
            "start": dates[r[0]],
            "end": dates[r[-1]],
            "days": len(r),
            "min": float(v.min()),
            "min_date": dates[r[j]],
            "depth": float(-v.min()),
            "was_westerly": was_westerly,
            "reversal": bool(was_westerly and -v.min() >= min_depth),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "ssw_archive.json"))
    ap.add_argument("--min-days", type=int, default=4)
    ap.add_argument("--level", type=int, default=10)
    ap.add_argument("--domain", default="60",
                    help="the domain the catalogue is built for")
    ap.add_argument("--min-depth", type=float, default=5.0,
                    help="how deep (m/s) the easterly flow must get")
    a = ap.parse_args()

    z = np.load(SERIES)
    dates = [str(d) for d in z["dates"]]
    levs = list(z["levels"])
    li = levs.index(a.level)

    # how much of the record is actually there?  1959-1990 has only
    # October-December, so the early winters are counted from what exists.
    year = np.array([int(d[:4]) for d in dates])
    coverage = {}
    for y in sorted(set(year.tolist())):
        coverage[str(y)] = int((year == y).sum())
    n_full = sum(1 for v in coverage.values() if v >= 360)

    out = {"level": a.level, "min_days": a.min_days,
           "min_depth": a.min_depth, "domain": a.domain or "60",
           "source": "ERA5 (ARCO) zonal-mean wind, daily 12Z, all 37 levels",
           "record": {"start": dates[0], "end": dates[-1],
                      "days": len(dates), "full_years": n_full,
                      "coverage": coverage},
           "domains": {}}
    for dom in ([a.domain] if a.domain else DOMAINS):
        series = z[f"u_{dom}"]
        evs = events_for(series, dates, li, min_days=a.min_days,
                         min_depth=a.min_depth)
        # the level at which each event reached its minimum
        for e in evs:
            i = dates.index(e["start"])
            j = dates.index(e["end"])
            block = series[:, i:j + 1]
            if np.isnan(block).all():
                e["min_level"] = None
                continue
            prof = np.nanmin(block, axis=1)
            e["min_level"] = int(levs[int(np.argmin(prof))])
        # the events that are real reversals
        rev = [e for e in evs if e["reversal"]]
        # group by winter (Nov-May spans the year end)
        by_winter = {}
        for e in rev:
            m = int(e["start"][5:7])
            y = int(e["start"][:4])
            w = y + 1 if m <= 4 else y
            by_winter.setdefault(w, []).append(e)
        out["domains"][dom] = {
            "events": rev,
            "n_events": len(rev),
            "n_winters": len(by_winter),
            "winters_with_event": sorted(by_winter),
            "by_winter": {str(k): v for k, v in sorted(by_winter.items())},
            "strongest": max(rev, key=lambda e: e["depth"]) if rev else None,
        }
        print(f"{dom}: {len(rev)} reversal events in {len(by_winter)} winters",
              flush=True)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(out, fh, indent=1)
    print("wrote", a.out, os.path.getsize(a.out) // 1024, "KB")


if __name__ == "__main__":
    main()

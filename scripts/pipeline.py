#!/usr/bin/env python3
"""
Run store + auto-update pipeline for the forecast site.

    python3 scripts/pipeline.py resolve          # newest published cycle per source
    python3 scripts/pipeline.py sync --level 10 --lat 60
    python3 scripts/pipeline.py list             # what is in the store

`sync` is what the watcher runs 4x/day (02, 08, 14, 20 UTC - two hours after
each 6-hourly suite is disseminated). It resolves the newest cycle, fetches any
model that is missing for that cycle, renders the four tab figures, and updates
data/store/latest.json.

Nothing is re-fetched if the series file already exists, so a re-run is cheap.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import site_meta
STORE = os.path.join(ROOT, "data", "store")
FIGDIR = os.path.join(STORE, "figures")
SELDIR = os.path.join(STORE, "series")
PY = (os.environ.get("PIPELINE_PYTHON")
      or (os.path.join(ROOT, "venv", "bin", "python")
          if os.path.exists(os.path.join(ROOT, "venv", "bin", "python"))
          else sys.executable))
for d in (STORE, FIGDIR, SELDIR):
    os.makedirs(d, exist_ok=True)

ECMWF_LIST = "https://data.ecmwf.int/forecasts/"
S3 = "https://{bucket}.s3.amazonaws.com/?list-type=2&prefix={prefix}&delimiter=/"

# model key -> (tab, label, family, kind)
MODELS = {
    "aifs-single": ("ai", "ECMWF AIFS Single", "ecmwf", "AI"),
    "aifs-ens": ("ai", "ECMWF AIFS ENS", "ecmwf", "AI"),
    "ifs": ("physics", "ECMWF IFS", "ecmwf", "Physics"),
    "eps": ("physics", "ECMWF EPS", "ecmwf", "Physics"),
    "gfs": ("physics", "NOAA GFS", "ncep", "Physics"),
    "gefs": ("physics", "NOAA GEFS", "ncep", "Physics"),
    "gefs_ctl": ("physics", "NOAA GEFS control", "ncep", "Physics"),
    "gefs_members": ("ensembles", "NOAA GEFS members", "ncep", "Ensemble members"),
}
TABS = [("physics", "Physics-based"), ("ai", "AI"),
        ("hybrid", "Hybrid (physics + AI)"), ("ensembles", "Ensembles (members)")]

# The domains the site offers (see site_meta.DOMAINS): a latitude, the NH
# aggregate or the polar cap. Free - the zonal profile of every member is
# already in the store.
LATS = tuple(site_meta.LAT_ORDER)
LAT_LABELS = site_meta.LAT_LABELS

# The levels the site offers, and which models have data at each of them.
# 10 hPa is the SSW level and has everything; the stratospheric levels have
# the cheap series only (the AIFS ENS / EPS members are a 6 GB fetch per
# level per cycle - not worth it for a chart).
ALL_MODELS_FOR_LEVEL = ("aifs-ens", "aifs-single", "ifs", "eps", "gfs",
                         "gefs", "gefs_ctl", "gefs_members")
CHEAP_MODELS_FOR_LEVEL = ("aifs-single", "ifs", "gfs", "gefs", "gefs_ctl")
LEVELS = {10: ALL_MODELS_FOR_LEVEL, 3: CHEAP_MODELS_FOR_LEVEL,
          1: CHEAP_MODELS_FOR_LEVEL}
DEFAULT_LEVEL = 10

# models that must be present for a complete cycle, and the slow extras
CORE = ("aifs-ens", "aifs-single", "ifs", "eps", "gfs", "gefs", "gefs_ctl")
EXTRA = ("gefs_members",)
ALL_MODELS = CORE + EXTRA

# The 06z and 18z suites carry no ECMWF control (IFS) and no ECMWF EPS:
# ECMWF only publishes those two products out to 144 h in the 06z/18z runs
# (the 00z/12z runs go to 240 h), so plotting them would show a forecast that
# simply stops early. They are left off the chart for those cycles.
NO_ECMWF_OPS = ("ifs", "eps")


def cycle_hour(cycle):
    return int(cycle[8:10])


def models_for_cycle(cycle):
    """Models included for a given cycle."""
    if cycle_hour(cycle) in (6, 18):
        return tuple(m for m in ALL_MODELS if m not in NO_ECMWF_OPS)
    return ALL_MODELS


def sh(cmd, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    p = subprocess.run(cmd, env=e, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def log(*a):
    print(f"[{datetime.now(timezone.utc):%H:%M:%SZ}]", *a, flush=True)


# ------------------------------------------------------------ cycle resolvers
def _get(url, tries=3):
    import requests
    for t in range(tries):
        try:
            r = requests.get(url, timeout=(15, 90))
            if r.status_code == 200:
                return r.text
        except Exception:
            pass
        time.sleep(2)
    return ""


# AIFS ENS is disseminated step by step: the 360 h file appears ~9-10 h after
# the initialisation, so waiting for it would push every cycle past its 9 h
# publication deadline. We therefore resolve the LARGEST horizon the archive
# has actually published and fetch exactly that - the chart shows what has been
# disseminated, and the next poll picks up the rest.
HORIZONS = (360, 240, 120, 72)
MIN_HORIZON = 72          # a cycle is only publishable with at least 3 days out


def ecmwf_horizon(date, cyc):
    """Largest published AIFS ENS horizon (h) for that cycle, or None."""
    import requests
    for h in HORIZONS:
        url = (f"{ECMWF_LIST}{date}/{cyc}z/aifs-ens/0p25/enfo/"
               f"{date}{cyc}0000-{h}h-enfo-cf.index")
        try:
            r = requests.get(url, timeout=(10, 25))
            if r.status_code == 200 and r.text.startswith("{"):
                return h
        except Exception:
            pass
    return None


def resolve_ecmwf():
    """Newest ECMWF cycle with a usable AIFS ENS -> (date, cyc, horizon)."""
    today = datetime.now(timezone.utc)
    for back in range(0, 5):
        d = (today - timedelta(days=back)).strftime("%Y%m%d")
        for c in ("18", "12", "06", "00"):
            h = ecmwf_horizon(d, c)
            if h is not None and h >= MIN_HORIZON:
                return d, c, h
    return None, None, None


def _s3_prefixes(bucket, prefix):
    """Common prefixes under a prefix, following pagination; returns the last
    page (lexicographically newest) because S3 caps a page at 1000 keys."""
    import requests
    token, last = None, []
    for _ in range(40):
        u = S3.format(bucket=bucket, prefix=prefix)
        if token:
            u += f"&continuation-token={token}"
        try:
            r = requests.get(u, timeout=(15, 90))
        except Exception:
            return last
        if r.status_code != 200:
            return last
        pre = re.findall(r"<Prefix>([^<]+)</Prefix>", r.text)[1:]
        if pre:
            last = pre
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", r.text)
        if not m:
            break
        token = m.group(1)
    return last


def resolve_ncep(bucket, name):
    """Newest NCEP cycle whose 16-day (f384) files are already published."""
    import requests
    stem = {"gfs": "atmos/gfs.t{c}z.pgrb2.0p25",
            "gefs": "atmos/pgrb2ap5/geavg.t{c}z.pgrb2a.0p50"}[name]
    today = datetime.now(timezone.utc)
    for back in range(0, 6):
        d = (today - timedelta(days=back)).strftime("%Y%m%d")
        for c in ("18", "12", "06", "00"):
            key = f"{name}.{d}/{c}/{stem.format(c=c)}.f384"
            try:
                r = requests.head(f"https://{bucket}.s3.amazonaws.com/{key}", timeout=(15, 60))
                if r.status_code == 200:
                    return d, c
            except Exception:
                pass
    return None, None


# ------------------------------------------------------------------- fetching
def series_path(model, cycle, level):
    return os.path.join(SELDIR, f"{model}_{cycle}_u{level}.npz")


LEGACY = {   # model -> (family, existing filename template in data/)
    "aifs-ens": ("ecmwf", "aifs_{run}_u{lev}.npz"),
    "aifs-single": ("ecmwf", "aifs_single_{run}_u{lev}.npz"),
    "eps": ("ecmwf", "eps_{run}_u{lev}.npz"),
    "ifs": ("ecmwf", "ifs_ctl_{run}_u{lev}.npz"),
    "gfs": ("ncep", "gfs_ctl_{run}.npz"),
    "gefs": ("ncep", "gefs_mean_{run}.npz"),
    "gefs_ctl": ("ncep", "gefs_ctl_{run}.npz"),
    "gefs_members": ("ncep", "gefs_members_{run}.npz"),
}


def series_max_step(path):
    """Last forecast step stored in a series file (-1 if unknown)."""
    try:
        d = np.load(path, allow_pickle=True)
        if "steps" in d:
            return int(np.asarray(d["steps"]).max())
    except Exception:
        pass
    return -1


def fetch_model(model, date, cyc, level, max_step=None):
    """Fetch one model's series for a cycle. Returns the npz path or None.

    `max_step` is the horizon the archive has disseminated; when the stored
    series stops short of it we fetch again (ECMWF fills the horizon in over
    the hours after the initialisation).
    """
    out = series_path(model, f"{date}{cyc}", level)
    if os.path.exists(out):
        have = series_max_step(out)
        # a small shortfall is normal (a step or two may still be missing) -
        # only a materially shorter horizon is worth another fetch
        if max_step is None or have >= max_step - 24:
            log(f"  {model}: cached (to {have}h)")
            return out
        log(f"  {model}: cached only to {have}h - refetching to {max_step}h")
        try:
            os.remove(out)
        except OSError:
            pass
    run = f"{date}{cyc}"
    entry = LEGACY.get(model)
    if entry is None:
        log(f"  {model}: no fetcher known - skipped")
        return None
    fam, tmpl = entry
    famrun = f"{date}{cyc}00" if fam == "ecmwf" else f"{date}{cyc}"
    legacy = os.path.join(ROOT, "data", tmpl.format(run=famrun, lev=level))
    if legacy and os.path.exists(legacy):
        shutil.copy(legacy, out)
        log(f"  {model}: seeded from data/")
        return out
    if model == "aifs-ens":
        env = dict(RUN_DATE=date, CYCLE=cyc.zfill(2) + "00", LEVEL=str(level), OUT=out,
                   MAX_STEP=str(max_step or 360))
        rc, so, se = sh([PY, os.path.join(HERE, "fetch_aifs.py")], env)
    elif model == "aifs-single":
        env = dict(RUN_DATE=date, CYCLE=cyc.zfill(2) + "00", LEVEL=str(level), OUT=out,
                   MAX_STEP=str(max_step or 360))
        rc, so, se = sh([PY, os.path.join(HERE, "fetch_aifs_single.py")], env)
    elif model in ("eps", "ifs"):
        env = dict(RUN_DATE=date, CYCLE=cyc.zfill(2) + "00", LEVEL=str(level),
                   MAX_STEP=str(max_step or 360))
        mode = "eps" if model == "eps" else "control"
        rc, so, se = sh([PY, os.path.join(HERE, "fetch_eps.py"), "--mode", mode], env)
        src = (f"eps_{run}00_u{level}.npz" if model == "eps" else f"ifs_ctl_{run}00_u{level}.npz")
        src = os.path.join(ROOT, "data", src)
        if rc == 0 and os.path.exists(src):
            shutil.copy(src, out)
        else:
            return None
    elif model in ("gefs", "gfs", "gefs_members"):
        prod = {"gefs": "gefs_mean", "gfs": "gfs_ctl", "gefs_members": "gefs_members"}[model]
        rc, so, se = sh([PY, os.path.join(HERE, "fetch_noaa.py"), "--product", prod,
                         "--date", date, "--cyc", cyc, "--level", str(level),
                         "--out", out])
    else:
        return None
    if rc != 0 or not os.path.exists(out):
        log(f"  {model}: FAILED rc={rc} {se.strip()[:160]}")
        return None
    log(f"  {model}: fetched")
    return out


def fetch_gefs_control(date, cyc, level):
    out = os.path.join(SELDIR, f"gefs_ctl_{date}{cyc}_u{level}.npz")
    if os.path.exists(out):
        return out
    rc, so, se = sh([PY, os.path.join(HERE, "fetch_noaa.py"), "--product", "gefs_ctl",
                     "--date", date, "--cyc", cyc, "--level", str(level), "--out", out])
    return out if rc == 0 else None


# -------------------------------------------------------------------- rendering
def _series_for_tab(tab, cycle, level, lat):
    """Collect the series each tab needs from the store."""
    run = cycle
    allowed = set(models_for_cycle(cycle))
    S = {}

    spec = site_meta.DOMAINS[str(lat)]
    band = (0.0, 90.0) if spec["kind"] == "nh" else spec.get("range")

    def profile_of(z, lats):
        """The zonal profile reduced to the requested domain."""
        if spec["kind"] == "lat":
            i = int(np.abs(lats - spec["lat"]).argmin())
            return z[:, :, i] if z.ndim == 3 else z[:, i]
        return band_mean(z, lats, band[0], band[1])

    def add(key, path, member=None, zonal=True, lat_idx=None):
        if not path or not os.path.exists(path):
            return
        d = np.load(path, allow_pickle=True)
        if member is None:                      # scalar series (u60 / nh_mean)
            t = np.array(d["steps"]) if "steps" in d else None
            if spec["kind"] == "lat":
                vals = d["u60"] if "u60" in d else d.get("nh_mean")
            elif "zonal" in d:                  # the band mean from the profile
                vals = profile_of(np.asarray(d["zonal"], float), d["lats"])
            elif spec["kind"] == "nh" and "nh_mean" in d:
                vals = d["nh_mean"]
            else:
                return                          # this model has no band value
            S[key] = (t, vals)
        else:                                  # zonal profile per member
            if "zonal" not in d:
                return                          # this model keeps only the mean
            z = d["zonal"]
            lats = d["lats"]
            prof = profile_of(z, lats)
            t = d["steps"]
            S[key] = (t, prof)

    def p(model):
        if model not in allowed:
            return None
        return series_path(model, run, level)

    def has(model):
        path = p(model)
        return bool(path) and os.path.exists(path)

    add("aifs_mean", p("aifs-ens"), member="ctl")
    add("aifs_ctl", p("aifs-ens"), member="ctl")
    add("aifs_single", p("aifs-single"), member="all")
    add("eps_mean", p("eps"))
    add("ifs_ctl", p("ifs"))
    add("gefs_mean", p("gefs"))
    add("gefs_ctl", p("gefs_ctl"))
    add("gfs", p("gfs"))
    if tab == "ensembles":
        add("aifs_ens_members", p("aifs-ens"), member="members")
        add("eps_members", p("eps"), member="members")
        add("gefs_members", p("gefs_members"), member="members")
    # aifs mean/control need separate extraction below
    if has("aifs-ens"):
        d = np.load(p("aifs-ens"), allow_pickle=True)
        prof = profile_of(d["zonal"], d["lats"])
        t = d["steps"]
        pert = list(range(1, prof.shape[1]))
        S["aifs_mean"] = (t, prof[:, pert].mean(axis=1))
        S["aifs_ctl"] = (t, prof[:, 0])
        if tab == "ensembles":
            S["aifs_ens_members"] = (t, prof[:, pert])      # (nstep, nmember)
    if has("eps"):
        d = np.load(p("eps"), allow_pickle=True)
        S["eps_mean"] = (d["steps"], d["u60"])
        if "zonal" in d:
            S["eps_members"] = (d["steps"], profile_of(d["zonal"], d["lats"]))
    if has("ifs"):
        d = np.load(p("ifs"), allow_pickle=True)
        S["ifs_ctl"] = (d["steps"], d["u60"])
    for key in ("aifs_ens_members", "eps_members", "gefs_members"):
        if key in S:
            t, pr = S[key]
            S[key] = (t, pr.T)          # -> (nmember, nstep)
    return S


def band_mean(z, lats, lo=0.0, hi=90.0):
    """Area-weighted mean of a zonal profile over a latitude band.

    z: (nstep, nmember, nlat) or (nstep, nlat); lats: the latitude array.
    """
    z = np.asarray(z, float)
    lats = np.asarray(lats, float)
    sel = (lats >= lo) & (lats <= hi)
    w = np.cos(np.deg2rad(lats[sel]))
    if z.ndim == 3:
        return (z[:, :, sel] * w).sum(axis=2) / w.sum()
    return (z[:, sel] * w).sum(axis=1) / w.sum()


def nh_mean(z, lats):
    """Area-weighted NH (0-90N) mean of a zonal profile array."""
    return band_mean(z, lats, 0.0, 90.0)


def available_models(cycle, level):
    return {m for m in models_for_cycle(cycle)
            if os.path.exists(series_path(m, cycle, level))}


# bump when the fetch format changes (the store is re-rendered then)
DATA_VERSION = 3


def series_signature(cycle, level):
    """(model, last step) for every model in the store.

    The model SET alone does not tell you that a forecast horizon grew (ECMWF
    fills the AIFS horizon in over the hours after the initialisation) - this
    does, so the chart is re-rendered when the data actually got longer.
    """
    out = []
    for m in models_for_cycle(cycle):
        p = series_path(m, cycle, level)
        if os.path.exists(p):
            out.append((m, series_max_step(p)))
    rendered = (DATA_VERSION, tuple(LATS), tuple(t for t, _ in TABS) + ("ssw",))
    return (rendered, tuple(sorted(out)))


def cycle_deadline(cycle):
    """9 h after the model initialisation = when we publish even if incomplete."""
    t0 = datetime.strptime(cycle, "%Y%m%d%H")
    return t0 + timedelta(hours=9)


def cycle_is_complete(cyc, have_models):
    """All the models this cycle's suite needs are in the store."""
    want = set(models_for_cycle(cyc)) - set(EXTRA)
    return want <= set(have_models(cyc))


def pick_cycle(candidates, have_models, now=None):
    """Which cycle to publish.

    * the NEWEST cycle that is complete wins;
    * a newer cycle that is still missing models does NOT hijack an older
      complete cycle - we keep showing the complete one until the newer one is
      ready (or its 9 h deadline passes, then it goes up flagged PARTIAL).
    """
    now = now or datetime.utcnow()
    candidates = [c for c in candidates if c]
    if not candidates:
        return None, False
    newest = max(candidates)
    ready = [c for c in candidates if cycle_is_complete(c, have_models)]
    if ready and max(ready) == newest:
        return newest, True
    if now >= cycle_deadline(newest):
        return newest, False
    if ready:
        return max(ready), True
    return newest, False


def render_all(cycle, level, lat=None, lats=None, levels=None):
    """Render every tab for every (level, domain) of this cycle."""
    import render
    lats = tuple(lats or LATS)
    levels = tuple(levels or sorted(LEVELS))
    outs = []
    for lev in levels:
      for lat_i in lats:
        S = _series_for_tab("ensembles", cycle, lev, lat_i)   # superset: members
        if not S:
            continue
        t0 = pd.Timestamp(f"{cycle[:4]}-{cycle[4:6]}-{cycle[6:8]} {cycle[8:10]}:00")
        t0 = t0.tz_localize(None)
        clim_pick, clim_sd = render.load_clim(lev, lat_i, t0,
                                              t0 + pd.Timedelta(hours=360))
        analysis = float(S["aifs_ctl"][1][0]) if "aifs_ctl" in S else float("nan")
        has_members = any(k in S for k in ("aifs_ens_members", "gefs_members",
                                           "eps_members"))
        for tab, _ in TABS:
            if tab in ("ensembles", "ssw") and not has_members:
                continue
            if not tabS:
                continue                      # nothing to draw at this level
            tabS = dict(S)
            if tab == "physics":
                for k in ("aifs_mean", "aifs_ctl", "aifs_single"):
                    tabS.pop(k, None)
            if tab == "ai":
                for k in ("eps_mean", "ifs_ctl", "gefs_mean", "gefs_ctl", "gfs"):
                    tabS.pop(k, None)
            if tab == "ensembles":
                tabS = {k: v for k, v in S.items() if k in
                        ("aifs_mean", "aifs_ctl", "eps_mean", "gefs_mean",
                         "aifs_ens_members", "eps_members", "gefs_members")}
            out = os.path.join(FIGDIR, f"{tab}_{cycle}_u{lev}_{lat_i}.png")
            render.render_tab(tab, tabS, clim_pick, clim_sd, out, lev, lat_i,
                              cycle, analysis)
            outs.append(out)
            log(f"  rendered {os.path.basename(out)}")
        # SSW watch: the reversal probability product (uses the member series)
        ssw_out = os.path.join(FIGDIR, f"ssw_{cycle}_u{lev}_{lat_i}.png")
        if render.render_ssw(S, clim_pick, clim_sd, ssw_out, lev, lat_i, cycle,
                             analysis):
            outs.append(ssw_out)
            log(f"  rendered {os.path.basename(ssw_out)}")
    return outs


# ------------------------------------------------------------------------ main
def sync(level=10, lat=60.0, only=None, members=True, force=False):
    """Fetch what is missing and render the four tabs. Idempotent."""
    lock = os.path.join(STORE, ".sync.lock")
    if os.path.exists(lock) and time.time() - os.path.getmtime(lock) < 3600:
        log("another sync is running (lock present) - exiting")
        return
    open(lock, "w").close()
    try:
        _sync(level, lat, only, members, force)
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def _sync(level, lat, only, members, force):
    status = {}
    stf = os.path.join(STORE, "status.json")
    if os.path.exists(stf):
        status = json.load(open(stf))
    ec_date, ec_cyc, ec_horizon = resolve_ecmwf()
    gfs_date, gfs_cyc = resolve_ncep("noaa-gfs-bdp-pds", "gfs")
    gefs_date, gefs_cyc = resolve_ncep("noaa-gefs-pds", "gefs")
    log(f"ECMWF newest: {ec_date} {ec_cyc}z (horizon {ec_horizon}h) | "
        f"GFS: {gfs_date} {gfs_cyc}z | GEFS: {gefs_date} {gefs_cyc}z")
    cyc_ec = f"{ec_date}{ec_cyc}" if ec_date else None   # 10-digit YYYYMMDDHH
    cyc_gfs = f"{gfs_date}{gfs_cyc}" if gfs_date else None
    cyc_gefs = f"{gefs_date}{gefs_cyc}" if gefs_date else None

    # ECMWF models (AIFS ENS/Single, IFS control, EPS) share the ECMWF cycle
    if ec_date:
        ec_models = models_for_cycle(f"{ec_date}{ec_cyc}")
        for m in ("aifs-ens", "aifs-single", "ifs", "eps"):
            if m not in ec_models:
                log(f"  {m}: not part of the {ec_cyc}z suite - skipped")
                continue
            if only and m not in only:
                continue
            fetch_model(m, ec_date, ec_cyc, level, max_step=ec_horizon)
        # the extra stratospheric levels: the cheap series only
        for lev, ms in LEVELS.items():
            if lev == level:
                continue
            for m in ms:
                if m in ("ifs", "eps") and m not in ec_models:
                    continue
                if only and m not in only:
                    continue
                fetch_model(m, ec_date, ec_cyc, lev, max_step=ec_horizon)
    # NCEP models
    if gfs_date:
        fetch_model("gfs", gfs_date, gfs_cyc, level)
    if gefs_date:
        fetch_model("gefs", gefs_date, gefs_cyc, level)
        if members:
            fetch_model("gefs_members", gefs_date, gefs_cyc, level)
        fetch_gefs_control(gefs_date, gefs_cyc, level)
    # the extra stratospheric levels (the cheap series only)
    for lev, ms in LEVELS.items():
        if lev == level:
            continue
        try:
            if gfs_date and "gfs" in ms:
                fetch_model("gfs", gfs_date, gfs_cyc, lev)
            if gefs_date:
                if "gefs" in ms:
                    fetch_model("gefs", gefs_date, gefs_cyc, lev)
                if "gefs_ctl" in ms:
                    fetch_gefs_control(gefs_date, gefs_cyc, lev)
        except Exception as e:
            log(f"  NOAA @{lev}h: FETCH ERROR {e!r} - continuing")

    # The figure cycle = the NEWEST cycle that is complete. Never the cycle with
    # the most models: the 06z/18z suites have no ECMWF IFS/EPS, so an older
    # 00z/12z cycle would always out-count them and the site would get stuck.
    candidates = [c for c in (cyc_ec, cyc_gfs, cyc_gefs) if c]

    def have_models(cyc):
        return {m for m in models_for_cycle(cyc)
                if os.path.exists(series_path(m, cyc, level))}

    newest = max(candidates)
    cycle, core_ok = pick_cycle(candidates, have_models)
    if cycle is None:
        log("no cycles available")
        return
    have = available_models(cycle, level)
    wanted = models_for_cycle(cycle)
    past_deadline = datetime.utcnow() >= cycle_deadline(cycle)

    st = json.load(open(stf)) if os.path.exists(stf) else {}
    rendered = st.get("rendered_models", {})
    prev = set(rendered.get(cycle, []))
    prev_sig = tuple(tuple(x) for x in st.get("rendered_sig", {}).get(cycle, []))
    sig = series_signature(cycle, level)

    def missing_figures(cyc):
        return [t for t, _ in TABS
                if not os.path.exists(os.path.join(FIGDIR, f"{t}_{cyc}_u{level}_60.png"))]

    changed = (prev != have) or (prev_sig != sig) or bool(missing_figures(cycle))
    if missing_figures(cycle):
        log(f"  cycle {cycle}: {len(missing_figures(cycle))} figure(s) missing "
            f"-> re-rendering")

    if not force and not changed and prev:
        log(f"cycle {cycle}: model set unchanged ({len(have)} models) - nothing to do")
        if cycle != newest:
            miss = sorted((set(models_for_cycle(newest)) - set(EXTRA)) - have_models(newest))
            log(f"  (waiting for the {newest[8:10]}z cycle: {', '.join(miss)})")
        outs = [os.path.join(FIGDIR, f) for f in os.listdir(FIGDIR)
                if f"_{cycle}_" in f and f.endswith(".png")]
    elif not core_ok and not past_deadline:
        missing = sorted((set(wanted) - set(EXTRA)) - have)
        log(f"cycle {cycle}: waiting for {len(missing)} more model(s) "
            f"({', '.join(missing)}) - not publishing a partial cycle")
        # the store is still written below, so its layout never goes stale
        outs = [os.path.join(FIGDIR, f) for f in os.listdir(FIGDIR)
                if f"_{cycle}_" in f and f.endswith(".png")]
        changed = False
    else:
        tag = "complete" if core_ok else "PARTIAL (deadline reached)"
        log(f"rendering cycle {cycle} [{tag}] - models: {', '.join(sorted(have))}")
        outs = render_all(cycle, level, lat)
        rendered[cycle] = sorted(have)
        st["rendered_models"] = rendered
        st.setdefault("rendered_sig", {})[cycle] = [list(x) for x in sig]
        st["rendered_cycle"] = cycle
        st["partial"] = not core_ok
        json.dump(st, open(stf, "w"), indent=2)

    # history: the main domain keeps KEEP cycles, the extra domains fewer
    # (the store is committed to the repo and uploaded to Pages)
    KEEP, KEEP_OTHER = 14, 2
    all_cycles = sorted({f.split("_")[1] for f in os.listdir(FIGDIR)})
    keep_main = set(all_cycles[-KEEP:])
    keep_other = set(all_cycles[-KEEP_OTHER:])
    for f in os.listdir(FIGDIR):
        parts = f.split("_")
        cyc = parts[1]
        lat = parts[3].split(".")[0] if len(parts) > 3 else "60"
        lev = parts[2].replace("u", "") if len(parts) > 2 else str(DEFAULT_LEVEL)
        keep = keep_main if (lat == "60" and lev == str(DEFAULT_LEVEL)) else keep_other
        if cyc not in keep:
            os.remove(os.path.join(FIGDIR, f))
    # store layout: {cycle: {"domain|level": {tab: figure}}}
    cycles = {}
    for f in sorted(os.listdir(FIGDIR)):
        parts = f.split("_")
        if len(parts) < 4:
            continue
        tab, cyc = parts[0], parts[1]
        lat = parts[3].split(".")[0]
        lev = parts[2].replace("u", "")
        cycles.setdefault(cyc, {}).setdefault(f"{lat}|{lev}", {})[tab] = f
    # which models are actually on each cycle's charts (06z/18z: no ECMWF IFS/EPS)
    def cycle_model_list(cyc):
        have = {m for m in models_for_cycle(cyc)
                if os.path.exists(series_path(m, cyc, level))}
        if have:
            return sorted(have)
        # older cycles: the series are long gone from the store, but a published
        # cycle is complete by construction -> fall back to the suite's model set
        return sorted(models_for_cycle(cyc))

    cycle_models = {c: cycle_model_list(c) for c in cycles}
    import render as _render
    latest = {"cycle": cycle, "level": level, "lat": lat,
              "cycle_label": _render.cycle_label(cycle),
              "updated": datetime.now(timezone.utc).isoformat(),
              "figures": {os.path.basename(p): f"/fig/{os.path.basename(p)}" for p in outs},
              "cycles": cycles,
              "cycle_models": cycle_models,
              "models": {m: {"label": MODELS[m][1], "tab": MODELS[m][0],
                              "family": MODELS[m][2], "kind": MODELS[m][3]}
                          for m in MODELS}}
    json.dump(latest, open(os.path.join(STORE, "latest.json"), "w"), indent=2)
    status["last_sync"] = datetime.now(timezone.utc).isoformat()
    status["cycles"] = {"ecmwf": cyc_ec, "gfs": cyc_gfs, "gefs": cyc_gefs}
    status["rendered"] = cycle
    json.dump(status, open(stf, "w"), indent=2)
    log("store updated ->", os.path.join(STORE, "latest.json"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["resolve", "sync", "list"])
    ap.add_argument("--level", type=int, default=10)
    ap.add_argument("--lat", type=float, default=60.0)
    ap.add_argument("--only", default=None)
    ap.add_argument("--quick", action="store_true", help="skip ensemble-member series")
    ap.add_argument("--force", action="store_true", help="re-render even if present")
    a = ap.parse_args()
    if a.cmd == "resolve":
        print("ECMWF:", resolve_ecmwf())
        print("GFS  :", resolve_ncep("noaa-gfs-bdp-pds", "gfs"))
        print("GEFS :", resolve_ncep("noaa-gefs-pds", "gefs"))
    elif a.cmd == "sync":
        only = a.only.split(",") if a.only else None
        sync(a.level, a.lat, only, members=not a.quick, force=a.force)
    else:
        f = os.path.join(STORE, "latest.json")
        print(json.dumps(json.load(open(f)), indent=2) if os.path.exists(f) else "store empty")
        print("\nseries files:")
        for p in sorted(os.listdir(SELDIR)):
            print("  ", p)
        print("\nfigures:")
        for p in sorted(os.listdir(FIGDIR)):
            print("  ", p)


if __name__ == "__main__":
    main()

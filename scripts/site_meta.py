#!/usr/bin/env python3
"""
Shared model metadata for the two front ends (FastAPI app + static build).

The store keeps, for every cycle, the list of models that are actually on the
charts (`cycle_models`). The 06z/18z cycles carry no ECMWF IFS control and no
ECMWF EPS - ECMWF publishes those two products only out to 144 h in those runs
(the 00z/12z runs go to 240 h) - so they are left off the charts entirely and
must not be advertised in the model table either.
"""
import os

SOURCES = {
    "aifs-single": "ECMWF open data (0.25°)",
    "aifs-ens": "ECMWF open data (0.25°)",
    "ifs": "ECMWF open data (0.25°)",
    "eps": "ECMWF open data (0.25°)",
    "gfs": "NOAA open data (0.25°)",
    "gefs": "NOAA open data (0.5°)",
    "gefs_ctl": "NOAA open data (0.5°)",
    "gefs_members": "NOAA open data (0.5°)",
}

# The domains the site offers. A domain is a latitude, the NH aggregate or
# the polar cap - all of them are free, the zonal profiles are already stored.
DOMAINS = {
    "60":  {"label": "60˚N", "kind": "lat", "lat": 60.0},
    "70":  {"label": "70˚N", "kind": "lat", "lat": 70.0},
    "0":   {"label": "NH (0-90N)", "kind": "nh", "lat": 0.0},
    "cap": {"label": "Polar cap (65-90N)", "kind": "cap", "range": (65.0, 90.0)},
}
DEFAULT_LAT = "60"
LAT_ORDER = ["60", "70", "0", "cap"]
LAT_LABELS = {k: v["label"] for k, v in DOMAINS.items()}

# the levels the site offers
LEVELS = [(10, "10 hPa"), (1, "1 hPa")]
DEFAULT_LEVEL = 10


def store_key(domain, level):
    """The store key for a (domain, level) pair: '60|10'."""
    return f"{domain}|{level}"


def key_label(key):
    """'60˚N · 10 hPa' for a store key."""
    try:
        dom, lev = key.split("|")
        lname = dict(LEVELS).get(int(lev), f"{lev} hPa")
        return f"{DOMAINS.get(dom, {}).get('label', dom)} · {lname}"
    except Exception:
        return key


def lat_sort_key(lat):
    return (LAT_ORDER.index(lat) if lat in LAT_ORDER else 99, lat)


def key_sort_key(key):
    """The default domain+level first, then the rest."""
    try:
        dom, lev = key.split("|")
        return (0 if (dom == DEFAULT_LAT and int(lev) == DEFAULT_LEVEL) else 1,
                LAT_ORDER.index(dom) if dom in LAT_ORDER else 99,
                -int(lev))
    except Exception:
        return (99, 99, 0)

NO_OPS_NOTE = ("ECMWF IFS control and ECMWF EPS are not shown in the 06z/18z cycles "
               "— ECMWF publishes those two runs only out to 144 h, so a 15-day "
               "chart would just stop early. AIFS, GFS and GEFS run the full "
               "15–16 days and are shown in full.")


def cycle_hour(cycle):
    try:
        return int(str(cycle)[8:10])
    except Exception:
        return None


def _kind(key, m):
    if m.get("kind"):
        return m["kind"]
    if key.startswith("aifs"):
        return "AI"
    if key.endswith("members"):
        return "Ensemble members"
    return "Physics"


def model_rows(latest, cycle):
    """Model-table rows for one cycle (only what is really plotted)."""
    models = (latest or {}).get("models") or {}
    avail = set((latest or {}).get("cycle_models", {}).get(cycle) or models.keys())
    rows = []
    for key, m in models.items():
        if key not in avail:
            continue
        rows.append({
            "key": key,
            "label": m.get("label", key),
            "kind": _kind(key, m),
            "tab": m.get("tab", ""),
            "source": SOURCES.get(key, ""),
        })
    return rows


def models_by_cycle(latest):
    """{cycle: {"rows": [...], "note": str}} for every cycle the store knows."""
    cycles = (latest or {}).get("cycles") or {}
    return {c: {"rows": model_rows(latest, c), "note": cycle_note(c)}
            for c in cycles}


def cycle_note(cycle):
    if cycle_hour(cycle) in (6, 18):
        return NO_OPS_NOTE
    return ""

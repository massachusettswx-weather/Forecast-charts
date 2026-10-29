#!/usr/bin/env python3
"""
Read the ERA5 climatology from the small published artifact.

The raw ERA5 profiles (37 levels x 721 lats x ~11,000 days) stay on the
build machine - they are ~850 MB and the hosts should never carry them.
What the site actually needs is a per-calendar-day mean and sigma for each
(domain, level), which is a few hundred KB in total: that is what
scripts/era5_summary.py writes and what this module reads.

Falls back to the raw local cache when the artifact is not there yet (the
build machine has it), so the charts render either way.
"""
import json
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTIFACT = os.environ.get("ERA5_SUMMARY",
                           os.path.join(ROOT, "data", "era5_summary.json"))

# the artifact names the domains; the store/site key names them differently
DOMAIN_NAMES = {"60": "60N", "70": "70N", "0": "NH", "cap": "cap"}

_CACHE = {}


def load(path=None):
    """The parsed artifact (cached)."""
    p = path or ARTIFACT
    if p not in _CACHE:
        with open(p) as fh:
            _CACHE[p] = json.load(fh)
    return _CACHE[p]


def available(path=None):
    try:
        load(path)
        return True
    except Exception:
        return False


def _md_table(dates, values):
    """{'MM-DD-12': value} from the artifact's parallel date/value lists.

    The key format has to match the raw cache (month-day-hour), because the
    charts look the climatology up the same way in both cases.
    """
    out = {}
    for d, v in zip(dates, values):
        out[f"{d[5:]}-12"] = float(v)
    return out


def climatology(level, domain, path=None):
    """(mean, sigma, count) keyed by 'MM-DD' for one (domain, level).

    `domain` is the store key ('60', '70', '0', 'cap').
    """
    art = load(path)
    key = f"{DOMAIN_NAMES.get(domain, domain)}|{int(level)}"
    entry = (art.get("climatology") or {}).get(key)
    if not entry:
        raise KeyError(f"{key} is not in {path or ARTIFACT}")
    mean = _md_table(entry["dates"], entry["mean"])
    sd = _md_table(entry["dates"], entry["sigma"])
    n = _md_table(entry["dates"], entry["n"])
    return mean, sd, n


def record_min(level, domain, path=None):
    """(value, date) of the lowest daily value in the season of the record."""
    art = load(path)
    key = f"{DOMAIN_NAMES.get(domain, domain)}|{int(level)}"
    entry = ((art.get("domains") or {}).get(DOMAIN_NAMES.get(domain, domain))
             or {}).get(str(int(level)))
    if not entry or "record_min" not in entry:
        raise KeyError(f"no record minimum for {key}")
    return float(entry["record_min"]), entry["record_min_date"]


def mean_at(level, domain, month, day, path=None):
    """The climatological mean for one calendar day (NaN if missing)."""
    mean, _, _ = climatology(level, domain, path)
    return mean.get(f"{int(month):02d}-{int(day):02d}-12", float("nan"))

#!/usr/bin/env python3
"""
Regression tests for the cycle logic (no network, no data needed).

    python3 scripts/test_cycles.py

They pin down the behaviour the site depends on:

  * 06z/18z cycles carry no ECMWF IFS/EPS (ECMWF publishes those runs only to
    144 h), 00z/12z carry both;
  * the publication deadline is 9 h after the initialisation
    (00z -> 09Z, 06z -> 15Z, 12z -> 21Z, 18z -> 03Z);
  * the NEWEST complete cycle is published, a newer incomplete cycle does not
    hijack an older complete one, and past the deadline the incomplete cycle
    goes up flagged PARTIAL;
  * the cycle label shows the cycle HOUR ("18z 26 Oct 2026"), never the day.
"""
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pipeline as P
import render as R

FAILED = []


def eq(got, want, what):
    if got == want:
        print(f"  ok   {what}: {got}")
    else:
        FAILED.append(what)
        print(f"  FAIL {what}: got {got!r}, want {want!r}")


def models_for(cyc):
    """The model set a cycle's suite is expected to carry."""
    return set(P.models_for_cycle(cyc))


def have(cyc, extra=()):
    return set(models_for(cyc)) | set(extra)


print("suite contents")
eq("ifs" in models_for("2026102606"), False, "06z has no ECMWF IFS")
eq("eps" in models_for("2026102606"), False, "06z has no ECMWF EPS")
eq("ifs" in models_for("2026102618"), False, "18z has no ECMWF IFS")
eq("eps" in models_for("2026102618"), False, "18z has no ECMWF EPS")
eq("ifs" in models_for("2026102700"), True, "00z has ECMWF IFS")
eq("eps" in models_for("2026102700"), True, "00z has ECMWF EPS")
eq("ifs" in models_for("2026102612"), True, "12z has ECMWF IFS")
eq("eps" in models_for("2026102612"), True, "12z has ECMWF EPS")
eq("aifs-ens" in models_for("2026102606"), True, "06z has AIFS ENS")
eq("aifs-single" in models_for("2026102606"), True, "06z has AIFS Single")

print("\npublication deadlines (9 h after init)")
eq(P.cycle_deadline("2026102700"), datetime(2026, 10, 27, 9), "00z -> 09Z")
eq(P.cycle_deadline("2026102606"), datetime(2026, 10, 26, 15), "06z -> 15Z")
eq(P.cycle_deadline("2026102612"), datetime(2026, 10, 26, 21), "12z -> 21Z")
eq(P.cycle_deadline("2026102618"), datetime(2026, 10, 27, 3), "18z -> 03Z (next day)")

print("\ncycle selection")
now = datetime(2026, 10, 26, 12, 0)
# newest complete wins
c, ok = P.pick_cycle(["2026102612", "2026102618"], lambda x: have(x), now=now)
eq((c, ok), ("2026102618", True), "newest complete cycle is published")
# a newer INCOMPLETE cycle must not hijack an older complete one
c, ok = P.pick_cycle(["2026102612", "2026102618"],
                     lambda x: have(x) if x == "2026102612" else have(x) - {"gefs"},
                     now=now)
eq((c, ok), ("2026102612", True), "incomplete newer cycle does not hijack")
# ... but past its deadline it goes up, flagged PARTIAL
c, ok = P.pick_cycle(["2026102612", "2026102618"],
                     lambda x: have(x) if x == "2026102612" else have(x) - {"gefs"},
                     now=datetime(2026, 10, 27, 3, 30))
eq((c, ok), ("2026102618", False), "past the deadline the newer cycle is PARTIAL")
# the 06z suite (no IFS/EPS) is complete without them
c, ok = P.pick_cycle(["2026102606"], lambda x: have(x), now=now)
eq((c, ok), ("2026102606", True), "06z cycle counts as complete without IFS/EPS")
# a 06z cycle missing AIFS ENS is NOT complete
c, ok = P.pick_cycle(["2026102606"], lambda x: have(x) - {"aifs-ens"}, now=now)
eq((c, ok), ("2026102606", False), "06z cycle without AIFS ENS is incomplete")

print("\ncycle label")
eq(R.cycle_label("2026102618"), "18z 26 Oct 2026", "18z label")
eq(R.cycle_label("2026102606"), "06z 26 Oct 2026", "06z label")
eq(R.cycle_label("2026102612"), "12z 26 Oct 2026", "12z label")
eq(R.cycle_label("2026102700"), "00z 27 Oct 2026", "00z label")
eq(R.cycle_label("2026102618").split("z")[0], "18", "label hour is the cycle hour")

print(f"\n{'FAILED: ' + ', '.join(FAILED) if FAILED else 'all good'}")
sys.exit(1 if FAILED else 0)

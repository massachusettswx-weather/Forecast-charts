#!/usr/bin/env python3
"""
Build a static site for GitHub Pages from the run store.

    python3 scripts/build_site.py --out site_out

Copies the rendered figures and writes a self-contained index.html (same
template the FastAPI app uses) plus latest.json.
"""
import argparse
import json
import os
import shutil
import sys

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "data", "store")
FIGDIR = os.path.join(STORE, "figures")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import site_meta

TABS = [("physics", "Physics-based", "ECMWF IFS + EPS, NOAA GFS + GEFS"),
        ("ai", "AI", "ECMWF AIFS Single + AIFS ENS"),
        ("hybrid", "Hybrid (physics + AI)", "every model on one graph"),
        ("ensembles", "Ensembles (members)", "member spaghetti + mean/control"),
        ("ssw", "SSW watch", "% of members easterly for 24 h or more - the reversal probability")]

# The ERA5 archive does not change with the forecast cycle - it is the
# 1959-2020 reanalysis record, drawn once and carried by the site.
ARCHIVE_DIR = os.path.join(STORE, "archive")
ARCHIVE_JSON = os.path.join(ROOT, "data", "ssw_archive.json")


def _archive():
    """The archive tab: the catalogue and the event gallery."""
    if not os.path.isdir(ARCHIVE_DIR):
        return None
    cat = os.path.join(ARCHIVE_DIR, "catalogue.png")
    if not os.path.exists(cat):
        return None
    events = []
    for f in sorted(os.listdir(ARCHIVE_DIR)):
        if not f.startswith("event_") or not f.endswith(".png"):
            continue
        date = f[len("event_"):-len(".png")]
        events.append({"date": date, "img": "fig/" + f})
    if not os.path.exists(ARCHIVE_JSON):
        return None
    try:
        meta = json.load(open(ARCHIVE_JSON))
    except Exception:
        return None
    ev = {e["start"]: e for e in (meta.get("domains", {})
                                       .get(meta.get("domain", "60"), {})
                                       .get("events") or [])}
    for e in events:
        rec = ev.get(e["date"])
        if rec:
            e["min"] = rec["min"]
            e["days"] = rec["days"]
            e["level"] = rec.get("min_level")
    # every event of the catalogue, for the browser's markers
    all_ev = [{"start": r["start"], "min": r["min"], "days": r["days"],
               "end": r["end"], "min_level": r.get("min_level")}
              for r in (meta.get("domains", {})
                        .get(meta.get("domain", "60"), {})
                        .get("events") or [])]
    return {"catalogue": "fig/catalogue.png", "events": events,
            "events_json": json.dumps(all_ev),
            "record_start": (meta.get("record") or {}).get("start"),
            "record_end": (meta.get("record") or {}).get("end"),
            "n_events": meta.get("domains", {})
                                 .get(meta.get("domain", "60"), {})
                                 .get("n_events"),
            "level": meta.get("level")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "site_out"))
    a = ap.parse_args()
    out = a.out
    # start from an empty output folder: a stale figure from an earlier
    # cycle would otherwise be uploaded with the new site
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, "fig"), exist_ok=True)

    latest = {}
    f = os.path.join(STORE, "latest.json")
    if os.path.exists(f):
        latest = json.load(open(f))
    figs = latest.get("figures", {})

    cycles = latest.get("cycles", {})
    if not cycles:                      # fall back to the current cycle
        cycles = {latest.get("cycle", ""): {"60": {k: v for k, v in figs.items()}}}
    cyc_list = sorted(cycles.keys(), reverse=True)
    # the menu offers the published views (and only the ones the store has)
    lat_list = [k for k in site_meta.PUBLISHED_KEYS
                if any(k in (cycles.get(c) or {}) for c in cycles)]
    # the site shows ONE view (the default domain and level): the other views
    # are still being checked, so they are not offered yet
    def _default_view(c):
        for l in lat_list:
            if l == site_meta.store_key(site_meta.DEFAULT_LAT,
                                        site_meta.DEFAULT_LEVEL):
                return (cycles.get(c, {}).get(l) or {})
        return {}
    tabs = []
    for key, title, blurb in TABS:
        img = None
        for c in cyc_list:
            f = _default_view(c).get(key)
            if f:
                img = "fig/" + f
                break
        tabs.append(dict(key=key, title=title, blurb=blurb, img=img))

    # Copy only the figures the site actually serves. With the picker on
    # that is every published view of every cycle; with it off the site
    # shows the default view only, so the rest are not uploaded. The
    # figures stay in the store either way - they are just not carried.
    if site_meta.PICKER_ENABLED:
        upload_keys = site_meta.PUBLISHED_KEYS
    else:
        upload_keys = [site_meta.store_key(site_meta.DEFAULT_LAT,
                                            site_meta.DEFAULT_LEVEL)]
    need = set()
    for c in cyc_list:
        for key in upload_keys:
            for f in (cycles.get(c, {}).get(key) or {}).values():
                need.add(f)
    names = []
    for name in sorted(need):
        # the site serves the PNGs; the PDF twins are for the download link
        # only and are not uploaded
        if not name.endswith(".png"):
            continue
        src = os.path.join(FIGDIR, name)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(out, "fig", name))
            names.append(name)

    # the ERA5 archive: the catalogue, the event gallery and the browsable
    # daily series (one gzipped JSON per domain, fetched on demand)
    archive = _archive()
    if archive:
        for name in sorted(os.listdir(ARCHIVE_DIR)):
            if name.endswith(".png") or name.endswith(".json.gz"):
                shutil.copy(os.path.join(ARCHIVE_DIR, name),
                            os.path.join(out, "fig", name))
                names.append(name)
        archive["events_json"] = json.dumps(archive["events"])
        archive["record_start"] = archive.get("record_start", "1959-10-01")
        archive["record_end"] = archive.get("record_end", "2020-12-31")
        # the browser script travels with the site
        js = os.path.join(ROOT, "webapp", "static", "archive.js")
        if os.path.exists(js):
            os.makedirs(os.path.join(out, "static"), exist_ok=True)
            shutil.copy(js, os.path.join(out, "static", "archive.js"))

    from datetime import datetime, timezone
    cyc = latest.get("cycle", "")
    labels = {}
    for c in cyc_list:
        try:
            labels[c] = datetime.strptime(c, "%Y%m%d%H").strftime("%d %b %Y %H%M UTC")
        except Exception:
            labels[c] = c
    when = labels.get(cyc, cyc)
    stf = os.path.join(STORE, "status.json")
    _st = json.load(open(stf)) if os.path.exists(stf) else {}
    partial_badge = " \u2022 PARTIAL" if _st.get("partial") and _st.get("rendered_cycle") == cyc else ""
    try:
        upd = datetime.fromisoformat(latest.get("updated", ""))
        age_h = int((datetime.now(timezone.utc) - upd).total_seconds() // 3600)
    except Exception:
        age_h = None
    try:
        t0 = datetime.strptime(cyc, "%Y%m%d%H")
        valid_from = t0.strftime("%d %b")
        valid_to = (t0 + __import__("datetime").timedelta(hours=360)).strftime("%d %b")
    except Exception:
        valid_from = valid_to = ""

    env = Environment(loader=FileSystemLoader(os.path.join(ROOT, "webapp", "templates")),
                      autoescape=select_autoescape(["html"]))
    tpl = env.get_template("index.html")
    import json as _json
    html = tpl.render(tabs=tabs, cycle=cyc, when=when,
                      updated=latest.get("updated", ""),
                      level=latest.get("level", 10), lat=latest.get("lat", 60),
                      lat_list=lat_list, key_labels={k: site_meta.key_label(k) for k in lat_list},
                      default_key=site_meta.store_key(site_meta.DEFAULT_LAT,
                                                      site_meta.DEFAULT_LEVEL),
                      cycle_list=cyc_list, cycle_labels=labels,
                      cycles_json=_json.dumps(cycles),
                      age_hours=age_h, valid_from=valid_from, valid_to=valid_to,
                      sources=site_meta.SOURCES, partial_badge=partial_badge,
                      archive=archive,
                      picker_enabled=site_meta.PICKER_ENABLED)
    with open(os.path.join(out, "index.html"), "w") as fh:
        fh.write(html)
    json.dump(latest, open(os.path.join(out, "latest.json"), "w"), indent=2)
    open(os.path.join(out, ".nojekyll"), "w").close()
    print(f"static site built -> {out}  ({len(names)} figures, cycle {cyc or 'none'})")


if __name__ == "__main__":
    main()

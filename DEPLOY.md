# Deploying the forecast site

Two options. Both run the same pipeline; they differ only in where the site is
served from and what it costs.

---

## Option A — GitHub Pages (free, recommended)

Cost: **$0** for public repos. The only compute is a GitHub Actions runner that
wakes 4× a day for ~10 minutes.

### One-time setup

1. **Push this repo to GitHub.**

   ```bash
   git remote add origin git@github.com:<you>/<repo>.git
   git add -A
   git commit -m "forecast charts: pipeline, site, ERA5 cache"
   git push -u origin main
   ```

2. **Commit the slim ERA5 cache** (28 MB) so the runner never has to download
   the 295 MB full cache:

   ```bash
   git add -f data/era5_cache_slim        # .gitignore excludes data/era5_cache/, not _slim
   git commit -m "era5 climatology cache (slim)"
   git push
   ```

3. **Settings → Pages → Build and deployment → Source: `GitHub Actions`.**

4. **Settings → Actions → General → Workflow permissions: `Read and write`**
   (the workflow commits the refreshed figures back to the repo).

5. That is it. `.github/workflows/update.yml` runs on the cron
   `0 2,8,14,20 * * *` (UTC) — eight hours after each initialisation, i.e. two
   hours after each suite is disseminated — and publishes the site to
   `https://<you>.github.io/<repo>/`.

   You can also trigger it on demand: **Actions → update-forecast-charts →
   Run workflow**.

### If the sandbox home is reset

`/root/.ssh` can be wiped between sessions. The private key also lives at
`/workspace/.home/.ssh/github_deploy` (persistent volume) - restore with:

```bash
mkdir -p /root/.ssh && chmod 700 /root/.ssh
cp -n /workspace/.home/.ssh/github_deploy* /root/.ssh/
chmod 600 /root/.ssh/github_deploy
```

(The public key stays registered on GitHub, so nothing needs re-adding there.)

> **US DST note:** the cron is UTC, so it never changes. Only the local clock
> reading does: DST ends **Sun 1 Nov 2026** (02:00 EDT -> 01:00 EST), after
> which these runs land one hour *later* in local terms — 03Z is 11pm EDT
> before the change and **10pm EST** after it. All timestamps in the charts
> and API are UTC and unaffected.

### What the workflow does each cycle

```
resolve newest cycle (ECMWF + NCEP, by direct date probing)
  -> fetch only the series that are missing (AIFS ENS/Single, IFS, EPS, GFS, GEFS)
  -> render the four tab figures into data/store/figures/
  -> build the static site into site_out/
  -> commit data/store + site_out back to the repo
  -> upload-pages-artifact -> deploy-pages
```

### Notes

- The runner needs `eccodes` for GRIB decoding; the workflow installs it and
  tolerates failure (the cached series make a re-render work even if a fetch
  is skipped).
- If a suite is late, the resolver simply picks the newest cycle that is
  actually published — it never renders a half-written run.
- To change what is published, edit `TABS` in `scripts/render.py` and
  `MODELS` in `scripts/pipeline.py`.

---

## Option B — small VPS (~$4–5/month)

Best if you want the FastAPI app (JSON API, per-model series) rather than a
static page, or if you want the full 295 MB ERA5 cache locally.

```bash
# on the box
git clone <your repo> && cd <repo>
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python scripts/era5_cache.py build --start 10-01 --end 12-31   # once
./venv/bin/python scripts/pipeline.py sync --level 10 --lat 60            # first render

# systemd service for the API
sudo tee /etc/systemd/system/forecast-site.service <<'EOF'
[Unit]
Description=Forecast chart site
After=network.target

[Service]
WorkingDirectory=/srv/forecast
ExecStart=/srv/forecast/venv/bin/uvicorn webapp.app:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl enable --now forecast-site

# the watcher: 4x daily, 2 h after each suite
sudo tee /etc/cron.d/forecast-update <<'EOF'
0 2,8,14,20 * * * cd /srv/forecast && ./venv/bin/python scripts/pipeline.py sync --level 10 --lat 60 >> /var/log/forecast-update.log 2>&1
EOF

# nginx in front
sudo tee /etc/nginx/sites-available/forecast <<'EOF'
server {
    listen 80;
    server_name charts.example.com;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }
}
EOF
sudo ln -s /etc/nginx/sites-available/forecast /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

---

## Verifying a deploy

```bash
curl -s https://<you>.github.io/<repo>/latest.json | head        # published cycle
curl -sI https://<you>.github.io/<repo>/fig/hybrid_*.png | head  # figures served
```

And locally:

```bash
python3 scripts/pipeline.py resolve        # newest cycle per source
python3 scripts/pipeline.py list           # what is in the store
python3 scripts/pipeline.py sync           # fetch + render + publish
python3 scripts/build_site.py --out site_out
```

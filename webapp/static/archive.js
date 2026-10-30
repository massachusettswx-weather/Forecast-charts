// The ERA5 archive browser.
//
// The daily series for the record (1959-2020, every level, every day) is
// fetched on demand as a gzipped JSON, then drawn on a canvas. The reader
// picks a domain and a level and scrolls through six decades of the polar
// stratosphere, with the reversals marked.
(function () {
  const A = window.ARCHIVE || {};
  if (!A.enabled) return;

  const el = (id) => document.getElementById(id);
  const domSel = el('arch-dom');
  const levSel = el('arch-lev');
  const cvs = el('arch-canvas');
  if (!cvs || !domSel) return;
  const ctx = cvs.getContext('2d');
  const tip = el('arch-tip');
  const cache = {};
  let data = null, level = 10, domain = '60';
  let view = null;           // {i0, i1} the visible day range
  // the reversal events: they travel with the payload, so the markers
  // follow the domain the reader picked
  let EVENTS = [];

  function fmtDate(d) {
    const [y, m, dd] = d.split('-').map(Number);
    const M = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return `${dd} ${M[m-1]} ${y}`;
  }

  function load(dom) {
    if (cache[dom]) return Promise.resolve(cache[dom]);
    return fetch(`fig/u_${dom}.json.gz`)
      .then(r => {
        if (!r.ok) throw new Error(`fetch failed: ${r.status}`);
        return r.arrayBuffer();
      })
      .then(buf => {
        // gunzip in the browser: the readable goes into the transform, not
        // the other way round
        const stream = new Blob([buf]).stream()
          .pipeThrough(new DecompressionStream('gzip'));
        return new Response(stream).text();
      })
      .catch(async (err) => {
        // no DecompressionStream (older Safari): ask the server to unzip it
        const r = await fetch(`api/archive/u/${dom}`);
        if (!r.ok) throw new Error(`cannot load the series: ${err.message}`);
        return r.text();
      })
      .then(t => { cache[dom] = JSON.parse(t); return cache[dom]; });
  }

  let dateIndex = new Map();     // 'YYYY-MM-DD' -> day index

  function climFor(lev) {
    const c = (data.clim || {})[String(lev)];
    if (!c) return null;
    const m = {};
    c.dates.forEach((d, i) => { m[d] = [c.mean[i], c.sigma[i]]; });
    return m;
  }

  function draw() {
    if (!data) return;
    const li = data.levels.indexOf(level);
    const u = data.u[li];
    const clim = climFor(level);
    const W = cvs.width, H = cvs.height;
    const pad = { l: 58, r: 14, t: 12, b: 26 };
    const pw = W - pad.l - pad.r, ph = H - pad.t - pad.b;

    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, W, H);

    // the visible window: the last N years by default
    const n = data.dates.length;
    if (!view) view = { i0: n - 365 * 3, i1: n };
    const i0 = Math.max(0, view.i0), i1 = Math.min(n, view.i1);
    const nvis = i1 - i0;

    // the y range from the visible data
    let lo = Infinity, hi = -Infinity;
    for (let i = i0; i < i1; i++) {
      const v = u[i];
      if (v === null || v === undefined) continue;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    if (!isFinite(lo)) { lo = -10; hi = 10; }
    const padY = (hi - lo) * 0.08 || 1;
    lo -= padY; hi += padY;

    const X = (i) => pad.l + (i - i0) / Math.max(nvis - 1, 1) * pw;
    const Y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * ph;

    // the climatology band and line
    if (clim) {
      ctx.beginPath();
      let started = false;
      for (let i = i0; i < i1; i++) {
        const d = data.dates[i];
        const md = d.slice(5);
        const c = clim[md];
        if (!c) continue;
        const [m, s] = c;
        const x = X(i), y = Y(m + 2 * s);
        if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
      }
      for (let i = i1 - 1; i >= i0; i--) {
        const d = data.dates[i];
        const c = clim[d.slice(5)];
        if (!c) continue;
        const x = X(i), y = Y(c[0] - 2 * c[1]);
        ctx.lineTo(x, y);
      }
      ctx.closePath();
      ctx.fillStyle = 'rgba(180,185,190,0.35)';
      ctx.fill();

      ctx.beginPath();
      started = false;
      for (let i = i0; i < i1; i++) {
        const c = clim[data.dates[i].slice(5)];
        if (!c) continue;
        const x = X(i), y = Y(c[0]);
        if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
      }
      ctx.strokeStyle = '#000';
      ctx.lineWidth = 2.2;
      ctx.stroke();
    }

    // zero line
    ctx.strokeStyle = '#111';
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(pad.l, Y(0));
    ctx.lineTo(W - pad.r, Y(0));
    ctx.stroke();

    // the record
    ctx.strokeStyle = '#2f6fb5';
    ctx.lineWidth = 1.0;
    ctx.beginPath();
    let started = false;
    for (let i = i0; i < i1; i++) {
      const v = u[i];
      if (v === null || v === undefined) continue;
      const x = X(i), y = Y(v);
      if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
    }
    ctx.stroke();

    // the reversal events: a marker at the start of each, and a dot at the
    // depth it reached
    const iStart = data.dates[i0], iEnd = data.dates[i1 - 1];
    for (const e of EVENTS) {
      if (!e.start || e.start < iStart || e.start > iEnd) continue;
      const i = dateIndex.get(e.start);
      if (i === undefined || i < i0 || i >= i1) continue;
      const x = X(i);
      ctx.strokeStyle = 'rgba(190,40,40,0.75)';
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.moveTo(x, pad.t);
      ctx.lineTo(x, H - pad.b);
      ctx.stroke();
      ctx.fillStyle = 'rgba(190,40,40,0.9)';
      ctx.beginPath();
      ctx.arc(x, Y(e.min), 3.4, 0, Math.PI * 2);
      ctx.fill();
    }

    // the axes
    ctx.strokeStyle = '#c8ccd0';
    ctx.lineWidth = 1;
    ctx.strokeRect(pad.l, pad.t, pw, ph);

    ctx.fillStyle = '#333';
    ctx.font = '11px system-ui, sans-serif';
    ctx.textAlign = 'right';
    const ticks = 6;
    for (let k = 0; k <= ticks; k++) {
      const v = lo + (hi - lo) * k / ticks;
      const y = Y(v);
      ctx.fillText(v.toFixed(0), pad.l - 6, y + 3.5);
      ctx.strokeStyle = '#e6e9ec';
      ctx.beginPath();
      ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y);
      ctx.stroke();
    }
    ctx.textAlign = 'center';
    const nlab = Math.min(7, Math.max(2, Math.floor(pw / 130)));
    for (let k = 0; k <= nlab; k++) {
      const i = i0 + Math.round((i1 - 1 - i0) * k / nlab);
      const x = X(i);
      ctx.fillStyle = '#333';
      ctx.fillText(fmtDate(data.dates[i]), x, H - pad.b + 15);
    }
    ctx.textAlign = 'left';
    ctx.fillStyle = '#111';
    ctx.font = '600 12px system-ui, sans-serif';
    ctx.fillText('Zonal wind (m/s)', 8, pad.t + 12);

    A.onView && A.onView({ i0, i1, lo, hi });
  }

  // --- interaction
  let dragging = null;
  cvs.addEventListener('mousedown', (ev) => {
    if (!data || !view) return;
    dragging = { x: ev.offsetX, i0: view.i0, i1: view.i1 };
  });
  window.addEventListener('mouseup', () => { dragging = null; });
  cvs.addEventListener('mousemove', (ev) => {
    if (!data) return;
    const rect = cvs.getBoundingClientRect();
    const px = (ev.clientX - rect.left) * (cvs.width / rect.width);
    const pad = { l: 58, r: 14 };
    const pw = cvs.width - pad.l - pad.r;
    const nvis = view.i1 - view.i0;
    if (dragging) {
      const dx = px - dragging.x;
      const shift = Math.round(-dx / pw * nvis);
      const n = data.dates.length;
      let a = dragging.i0 + shift, b = dragging.i1 + shift;
      if (a < 0) { b -= a; a = 0; }
      if (b > n) { a -= (b - n); b = n; }
      view = { i0: a, i1: b };
      draw();
      return;
    }
    // the hover readout
    if (!view) return;
    const t = (px - pad.l) / pw;
    if (t < 0 || t > 1) { tip.style.display = 'none'; return; }
    const i = Math.round(view.i0 + t * (view.i1 - view.i0));
    if (i < 0 || i >= data.dates.length) { tip.style.display = 'none'; return; }
    const v = u_at(i);
    const c = climFor(level);
    const cm = c ? c[data.dates[i].slice(5)] : null;
    tip.style.display = 'block';
    tip.style.left = (ev.clientX - rect.left + 12) + 'px';
    tip.style.top = (ev.clientY - rect.top + 12) + 'px';
    let s = `<b>${fmtDate(data.dates[i])}</b><br>${level} hPa: ${v === null ? '—' : v.toFixed(2) + ' m/s'}`;
    if (cm) {
      const an = v - cm[0];
      s += `<br>anomaly ${an >= 0 ? '+' : ''}${an.toFixed(2)} m/s (${(an / cm[1]).toFixed(1)}σ)`;
    }
    tip.innerHTML = s;
  });
  cvs.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
  cvs.addEventListener('wheel', (ev) => {
    if (!data || !view) return;
    ev.preventDefault();
    const n = data.dates.length;
    const span = view.i1 - view.i0;
    const mid = (view.i0 + view.i1) / 2;
    const f = ev.deltaY > 0 ? 1.25 : 0.8;
    let s = Math.max(60, Math.min(n, Math.round(span * f)));
    let a = Math.round(mid - s / 2), b = a + s;
    if (a < 0) { b -= a; a = 0; }
    if (b > n) { a -= (b - n); b = n; }
    view = { i0: a, i1: b };
    draw();
  }, { passive: false });

  function u_at(i) {
    const li = data.levels.indexOf(level);
    const v = data.u[li][i];
    return v === null || v === undefined ? null : v;
  }

  // --- the controls
  function setLevels() {
    levSel.innerHTML = '';
    for (const l of data.levels) {
      const o = document.createElement('option');
      o.value = l;
      o.textContent = l + ' hPa';
      if (l === level) o.selected = true;
      levSel.appendChild(o);
    }
  }

  domSel.addEventListener('change', () => {
    domain = domSel.value;
    load(domain).then(d => {
      data = d;
      EVENTS = d.events || [];
      dateIndex = new Map(d.dates.map((d, i) => [d, i]));
      setLevels();
      view = null;
      draw();
    });
  });
  levSel.addEventListener('change', () => {
    level = parseInt(levSel.value, 10);
    draw();
  });
  const zoom = (f) => {
    if (!data || !view) return;
    const n = data.dates.length;
    const mid = (view.i0 + view.i1) / 2;
    let s = Math.max(60, Math.min(n, Math.round((view.i1 - view.i0) * f)));
    let a = Math.round(mid - s / 2), b = a + s;
    if (a < 0) { b -= a; a = 0; }
    if (b > n) { a -= (b - n); b = n; }
    view = { i0: a, i1: b };
    draw();
  };
  el('arch-in') && el('arch-in').addEventListener('click', () => zoom(0.6));
  el('arch-out') && el('arch-out').addEventListener('click', () => zoom(1.7));
  el('arch-all') && el('arch-all').addEventListener('click', () => {
    view = { i0: 0, i1: data.dates.length };
    draw();
  });

  // --- start
  load(domain).then(d => {
    data = d;
    EVENTS = d.events || [];
    dateIndex = new Map(d.dates.map((d, i) => [d, i]));
    setLevels();
    draw();
  });
})();

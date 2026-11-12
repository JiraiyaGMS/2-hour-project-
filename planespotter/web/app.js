/* planespotter live map.
 * MapLibre draws the map (and 3D buildings); deck.gl draws the planes at their real altitude.
 * All data comes from planespotter running on this computer: /api/config, /api/live,
 * /api/flights and /api/info.
 */
(() => {
  'use strict';

  const FT = 0.3048;              // metres per foot
  const MPP_Z0 = 78271.517;       // metres per pixel at zoom 0 on the equator (MapLibre's 512 px tiles)
  const MI = 1609.344;            // metres per mile
  const KT = 0.514444;            // metres per second per knot
  const LIVE_EVERY_MS = 5000;
  const HISTORY_EVERY_MS = 60000;
  const LIST_PAGE = 300;
  const STYLE = {
    dark: 'https://tiles.openfreemap.org/styles/dark',
    light: 'https://tiles.openfreemap.org/styles/positron',
  };

  const $ = (sel) => document.querySelector(sel);
  const saved = {
    get(key, fallback) {
      try { const v = localStorage.getItem('planespotter.' + key); return v === null ? fallback : JSON.parse(v); }
      catch { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem('planespotter.' + key, JSON.stringify(value)); } catch { /* private mode */ }
    },
  };

  // ---------- colours (read from the CSS so the map matches the theme) ----------
  const dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  function rgb(hex) {
    let h = hex.replace('#', '');
    if (h.length === 3) h = h.split('').map((c) => c + c).join('');
    const n = parseInt(h, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const C = {
    accent: rgb(cssVar('--accent')), blue: rgb(cssVar('--blue')), select: rgb(cssVar('--select')),
    ink: rgb(cssVar('--ink')), panel: rgb(cssVar('--panel')), muted: rgb(cssVar('--muted')),
  };
  const a = (color, alpha) => [...color, alpha];
  // Flat things on the ground (zone, heat map) let each other show through instead of
  // flickering, while 3D buildings still hide them.
  const GROUND = { depthWriteEnabled: false, depthCompare: 'less-equal' };

  // ---------- state ----------
  const state = {
    config: null,
    zone: [],
    mode: saved.get('mode', '3d'),
    tab: saved.get('tab', 'live'),
    days: saved.get('days', 7),
    sort: saved.get('sort', 'newest'),
    query: '',
    live: [],
    liveError: null,
    trails: new Map(),          // hex -> [[lon, lat, alt_m], ...]
    flights: [],
    historyView: [],            // flights after search + sort
    flightsLoaded: false,
    selected: null,             // { kind: 'live' | 'history', key }
    lastSelectedLive: null,
    info: new Map(),            // hex|callsign -> route + aircraft details
    infoQueue: [],
    infoBusy: 0,
    listLimit: LIST_PAGE,
    heat: saved.get('heat', false),
    heatDays: saved.get('heatDays', 7),
    heatData: null,
    whole: null,                // the selected flight's whole path: { key, status, path, p3, p2 }
    wholeAll: { flights: [], byId: new Map(), version: 0 },  // every spotted flight's whole path
    logoOK: new Set(),          // airline codes whose logo loaded
    logoBad: new Set(),         // ...and ones that have no logo
  };

  // ---------- small helpers ----------
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
  const compass = (deg) => (deg == null ? '' : COMPASS[Math.round((((deg % 360) + 360) % 360) / 45) % 8]);
  const fmtAlt = (ft) => (ft == null ? '—' : `${Math.round(ft).toLocaleString()} ft`);
  const fmtDist = (mi) => `${mi.toFixed(1)} mi`;
  const fmtSpeed = (kt) => (kt == null ? '—' : `${Math.round(kt)} kt`);
  const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const label = (f) => f.callsign || f.registration || f.hex.toUpperCase();
  const place = (code, city) => (city && code ? `${city} (${code})` : city || code || '?');
  const hasRoute = (i) => i && (i.origin || i.destination);
  const routeShort = (i) => (hasRoute(i) ? `${i.origin || '?'} → ${i.destination || '?'}` : '');
  const routeLong = (i) => (hasRoute(i) ? `${place(i.origin, i.origin_city)} → ${place(i.destination, i.destination_city)}` : '');
  const sameDay = (x, y) => x.toDateString() === y.toDateString();

  function dayLabel(ts) {
    const d = new Date(ts * 1000);
    const today = new Date();
    const yesterday = new Date(); yesterday.setDate(today.getDate() - 1);
    if (sameDay(d, today)) return 'Today';
    if (sameDay(d, yesterday)) return 'Yesterday';
    return d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' });
  }

  /** [lon, lat] of the point `metres` away from (lat, lon) towards `bearing`. */
  function destination(lat, lon, bearing, metres) {
    const R = 6371008.8, d = metres / R, b = bearing * Math.PI / 180;
    const p1 = lat * Math.PI / 180, l1 = lon * Math.PI / 180;
    const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b));
    const l2 = l1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
    return [l2 * 180 / Math.PI, p2 * 180 / Math.PI];
  }
  const circle = (lat, lon, miles, n = 128) =>
    Array.from({ length: n + 1 }, (_, i) => destination(lat, lon, (i * 360) / n, miles * MI));

  async function getJSON(url) {
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      try { msg = (await res.json()).error || msg; } catch { /* not json */ }
      throw new Error(msg);
    }
    return res.json();
  }

  // ---------- live planes ----------
  /** Where a plane is now, gliding along its heading since the last position report. */
  function livePos(plane, now) {
    // Glide for up to 5 minutes between updates (they can be minutes apart to stay within adsb.lol's limit).
    const dt = Math.min(Math.max((now - plane.fixAt) / 1000, 0), 300);
    if (!plane.speed_kt || plane.heading == null) return [plane.lon, plane.lat];
    return destination(plane.lat, plane.lon, plane.heading, plane.speed_kt * KT * dt);
  }

  function updateTrails() {
    const seen = new Set();
    for (const p of state.live) {
      seen.add(p.hex);
      let trail = state.trails.get(p.hex);
      if (!trail) state.trails.set(p.hex, (trail = []));
      const last = trail[trail.length - 1];
      if (!last || last[0] !== p.lon || last[1] !== p.lat) trail.push([p.lon, p.lat, (p.alt_ft || 0) * FT]);
      if (trail.length > 90) trail.shift();
    }
    for (const hex of [...state.trails.keys()]) if (!seen.has(hex)) state.trails.delete(hex);
  }

  async function pollLive() {
    if (!document.hidden) {
      try {
        const data = await getJSON('api/live');
        const now = performance.now();
        // How old each position is: time since the last check, plus how stale it already was then.
        const sinceCheck = Math.max(0, (data.time || 0) - (data.updated || data.time || 0));
        state.live = data.aircraft.map((p) => ({ ...p, fixAt: now - (sinceCheck + (p.seen_pos || 0)) * 1000 }));
        state.liveMeta = { interval: data.budget ? data.budget.interval_s : 0, paused: data.paused_s || 0,
                           waiting: !!data.waiting };
        state.liveError = null;
        state.quietNow = !!data.quiet_now;
        updateTrails();
        queueInfo(state.live);
        const sel = state.selected;
        if (sel && sel.kind === 'live') {
          const found = state.live.find((p) => p.hex === sel.key);
          if (found) state.lastSelectedLive = found;
        }
      } catch (err) {
        state.liveError = err instanceof TypeError ? 'offline' : err.message;
      }
      updateStatus();
      if (state.tab === 'live') renderList();
      renderDetail();
    }
    setTimeout(pollLive, LIVE_EVERY_MS);
  }

  // ---------- route + aircraft details (looked up a few at a time) ----------
  const infoKey = (p) => `${p.hex}|${p.callsign || ''}`;
  const infoFor = (p) => state.info.get(infoKey(p)) || null;

  function queueInfo(planes, first = false) {
    for (const p of planes) {
      const key = infoKey(p);
      if (state.info.has(key)) continue;
      state.info.set(key, null);
      if (first) state.infoQueue.unshift(p); else state.infoQueue.push(p);
    }
    pumpInfo();
  }

  let listTimer = 0;
  function renderListSoon() {
    clearTimeout(listTimer);
    listTimer = setTimeout(renderList, 250);
  }

  function pumpInfo() {
    while (state.infoBusy < 2 && state.infoQueue.length) {
      const p = state.infoQueue.shift();
      state.infoBusy++;
      const q = new URLSearchParams({ hex: p.hex, callsign: p.callsign || '', lat: p.lat, lon: p.lon });
      getJSON(`api/info?${q}`)
        .then((info) => {
          state.info.set(infoKey(p), info);
          if (state.tab === 'live') renderListSoon();
          renderDetail();
        })
        .catch(() => state.info.set(infoKey(p), {}))
        .finally(() => { state.infoBusy--; pumpInfo(); });
    }
  }

  // ---------- history ----------
  async function loadFlights() {
    try {
      const data = await getJSON(`api/flights?days=${state.days}`);
      for (const f of data.flights) {
        f.path3d = f.path.map((p) => [p[1], p[0], (p[2] || 0) * FT]);
        f.path2d = f.path.map((p) => [p[1], p[0], 0]);
      }
      state.flights = data.flights;
      state.flightsLoaded = true;
    } catch (err) {
      if (!state.flightsLoaded) state.flights = [];
    }
    if (state.tab === 'history') renderList(); else updateHistoryView();
  }

  function haystack(f, info) {
    const i = info || {};
    return [f.callsign, f.registration, f.hex, f.type, f.airline, f.origin, f.destination,
      f.origin_city, f.destination_city, f.aircraft, i.airline, i.origin, i.destination,
      i.origin_city, i.destination_city, i.aircraft_desc, i.owner].filter(Boolean).join(' ').toLowerCase();
  }
  const matches = (f, info) => !state.query || state.query.split(/\s+/).every((w) => haystack(f, info).includes(w));

  function updateHistoryView() {
    const list = state.flights.filter((f) => matches(f));
    const by = {
      newest: (x, y) => y.first_seen - x.first_seen,
      closest: (x, y) => x.closest_miles - y.closest_miles,
      lowest: (x, y) => (x.closest_alt_ft ?? 1e9) - (y.closest_alt_ft ?? 1e9),
    }[state.sort];
    state.historyView = list.sort(by);
    state.historyVersion = (state.historyVersion || 0) + 1;
    $('#history-count').textContent = state.historyView.length.toLocaleString();
  }

  // ---------- airline logos ----------
  /** 'DAL1140' -> 'DAL': airline callsigns start with the airline's ICAO code. */
  const airlineCode = (callsign) => { const m = /^([A-Z]{3})\d/.exec(callsign || ''); return m ? m[1] : null; };
  const SMALL_PLANE = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2c.8 0 1.3.9 1.3 2.1v5.4l7.9 4.7v2.1l-7.9-2.5v4.6l2.7 2v1.8L12 21.2l-4 1v-1.8l2.7-2v-4.6l-7.9 2.5v-2.1l7.9-4.7V4.1C10.7 2.9 11.2 2 12 2z"/></svg>';

  function logo(callsign, big = false) {
    const code = airlineCode(callsign);
    const cls = `airline-logo${big ? ' big' : ''}`;
    if (!code) return `<span class="${cls} ga" aria-hidden="true">${SMALL_PLANE}</span>`;
    if (state.logoBad.has(code)) return `<span class="${cls}" aria-hidden="true"><span class="code">${code}</span></span>`;
    return `<span class="${cls}" aria-hidden="true"><span class="code">${code}</span>`
      + `<img src="logo/${code}.png" alt="" loading="lazy" data-code="${code}"></span>`;
  }

  // Remember which logos exist, so the map only asks for ones that do.
  document.addEventListener('load', (e) => {
    if (e.target.tagName === 'IMG' && e.target.dataset.code) state.logoOK.add(e.target.dataset.code);
  }, true);
  document.addEventListener('error', (e) => {
    const img = e.target;
    if (img.tagName === 'IMG' && img.dataset.code) { state.logoBad.add(img.dataset.code); img.remove(); }
  }, true);

  // ---------- the list ----------
  function liveItem(p) {
    const info = infoFor(p) || {};
    const selected = isSelected('live', p.hex) ? ' selected' : '';
    const sub = [info.airline, info.aircraft_desc || p.type].filter(Boolean).join(' · ');
    return `<button class="item${selected}" data-kind="live" data-key="${esc(p.hex)}" role="listitem">
      ${logo(p.callsign)}
      <div class="main">
        <div class="title"><span class="callsign">${esc(label(p))}</span>${p.in_zone ? '<span class="badge">Overhead</span>' : ''}<span class="route">${esc(routeShort(info))}</span></div>
        <div class="sub">${esc(sub) || '&nbsp;'}</div>
      </div>
      <div class="meta"><div class="alt">${fmtAlt(p.alt_ft)}</div><div class="dist">${fmtDist(p.distance_miles)} ${compass(p.bearing)}</div></div>
    </button>`;
  }

  /** A plane that crossed your zone earlier and is still in the air somewhere else. */
  function awayItem({ w, f }) {
    const selected = isSelected('history', f.id) ? ' selected' : '';
    const pos = flyingNow(w, null, performance.now());
    const sub = [f.airline, f.aircraft || f.type].filter(Boolean).join(' · ');
    return `<button class="item${selected}" data-kind="history" data-key="${f.id}" role="listitem">
      ${logo(f.callsign)}
      <div class="main">
        <div class="title"><span class="callsign">${esc(label(f))}</span><span class="route">${esc(routeShort(f))}</span></div>
        <div class="sub">${esc(sub) || '&nbsp;'}</div>
      </div>
      <div class="meta">${pos ? `<div class="alt">${fmtAlt(pos.alt_ft)}</div><div class="dist">${esc(fromHome(pos.lat, pos.lon))}</div>`
        : `<div class="dist">over you at ${fmtTime(f.first_seen)}</div>`}</div>
    </button>`;
  }

  function historyItem(f) {
    const selected = isSelected('history', f.id) ? ' selected' : '';
    const sub = [f.airline, f.aircraft || f.type].filter(Boolean).join(' · ');
    const time = state.sort === 'newest' ? fmtTime(f.first_seen)
      : new Date(f.first_seen * 1000).toLocaleDateString([], { day: 'numeric', month: 'short' });
    return `<button class="item${selected}" data-kind="history" data-key="${f.id}" role="listitem">
      <div class="time">${time}${f.quiet ? '<br>🌙' : ''}</div>
      ${logo(f.callsign)}
      <div class="main">
        <div class="title"><span class="callsign">${esc(label(f))}</span>${isFlying(f.id) ? '<span class="badge air">In the air</span>' : ''}<span class="route">${esc(routeShort(f))}</span></div>
        <div class="sub">${esc(sub) || '&nbsp;'}</div>
      </div>
      <div class="meta"><div class="alt">${fmtAlt(f.closest_alt_ft)}</div><div class="dist">${fmtDist(f.closest_miles)} ${compass(f.bearing)}</div></div>
    </button>`;
  }

  const empty = (text) => `<div class="empty">${text}</div>`;
  const moreButton = (shown, total) =>
    total > shown ? `<button class="chip more" data-action="more">Show more (${(total - shown).toLocaleString()} left)</button>` : '';

  function renderList() {
    const el = $('#list');
    $('#live-count').textContent = state.live.length;
    if (state.tab === 'live') {
      const items = state.live.filter((p) => matches(p, infoFor(p)));
      // Planes that went over you and are still flying, but are now too far away for the live list.
      const nearby = new Set(state.live.map((p) => p.hex));
      const away = state.wholeAll.flights
        .filter((w) => w.flying && !nearby.has(w.hex))
        .map((w) => ({ w, f: flightById(w.id) }))
        .filter(({ f }) => f && matches(f));
      if (!items.length && !away.length) {
        el.innerHTML = empty(state.query ? 'No planes in the air match your search.'
          : state.liveError ? 'Waiting for live data…' : 'No planes nearby right now.');
        render();
        return;
      }
      el.innerHTML = items.slice(0, state.listLimit).map(liveItem).join('') + moreButton(state.listLimit, items.length)
        + (away.length ? `<div class="list-section">Went over you · still flying</div>${away.map(awayItem).join('')}` : '');
    } else {
      updateHistoryView();
      const items = state.historyView;
      if (!items.length) {
        el.innerHTML = empty(!state.flightsLoaded ? 'Loading…'
          : state.query ? 'No spotted flights match your search.'
            : 'No planes spotted in this time yet. Keep planespotter running and they’ll appear here.');
        render();
        return;
      }
      let html = '';
      let lastDay = '';
      for (const f of items.slice(0, state.listLimit)) {
        if (state.sort === 'newest') {
          const day = dayLabel(f.first_seen);
          if (day !== lastDay) { html += `<div class="day">${esc(day)}</div>`; lastDay = day; }
        }
        html += historyItem(f);
      }
      el.innerHTML = html + moreButton(state.listLimit, items.length);
    }
    render();
  }

  // ---------- selection + detail card ----------
  const isSelected = (kind, key) => state.selected && state.selected.kind === kind && state.selected.key === String(key);

  function selectedThing() {
    const sel = state.selected;
    if (!sel) return null;
    if (sel.kind === 'live') {
      const plane = state.live.find((p) => p.hex === sel.key);
      return plane ? { plane, lost: false } : state.lastSelectedLive ? { plane: state.lastSelectedLive, lost: true } : null;
    }
    const flight = state.flights.find((f) => String(f.id) === sel.key);
    return flight ? { flight } : null;
  }

  function select(kind, key, { fly = true, scroll = false } = {}) {
    state.selected = key == null ? null : { kind, key: String(key) };
    if (kind === 'live' && key != null) {
      const plane = state.live.find((p) => p.hex === String(key));
      state.lastSelectedLive = plane || null;
      if (plane) queueInfo([plane], true);
    }
    state.whole = null;
    if (state.selected) loadWhole();
    renderList();
    renderDetail();
    if (fly && state.selected) flyToSelected();
    if (scroll && state.selected) {
      const item = document.querySelector(`.item[data-kind="${kind}"][data-key="${CSS.escape(String(key))}"]`);
      if (item) item.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }

  function flyToSelected() {
    const thing = selectedThing();
    if (!thing) return;
    const center = thing.plane ? livePos(thing.plane, performance.now()) : [thing.flight.lon, thing.flight.lat];
    map.flyTo({ center, zoom: Math.max(map.getZoom(), 11.5), duration: 1100, essential: true });
  }

  // ---------- whole flights ----------
  const isFlying = (id) => { const w = state.wholeAll.byId.get(Number(id)); return !!(w && w.flying); };

  /** Every spotted flight's whole path, plus live positions for the ones still in the air. */
  async function loadWholeAll() {
    const days = state.tab === 'history' ? state.days : 1;
    try {
      const data = await getJSON(`api/whole?days=${days}`);
      const t = performance.now();
      for (const f of data.flights) {
        f.p3 = f.path.map((q) => [q[0], q[1], (q[2] || 0) * FT]);
        f.p2 = f.path.map((q) => [q[0], q[1], 0]);
        if (f.now) f.now.fixAt = t - (f.now.seen_pos || 0) * 1000;
      }
      state.wholeAll = {
        flights: data.flights, byId: new Map(data.flights.map((f) => [f.id, f])),
        version: state.wholeAll.version + 1,
      };
    } catch { /* keep what we had */ }
    renderListSoon();
    renderDetail();
    render();
  }

  /** Where a spotted flight that's still flying is right now (glides between updates). */
  function flyingNow(w, planesByHex, now) {
    const near = planesByHex && planesByHex.get(w.hex);
    if (near) return { lon: near.lon, lat: near.lat, alt_ft: near.p.alt_ft, heading: near.p.heading, near: true };
    if (!w.now) return null;
    const [lon, lat] = livePos({ ...w.now, fixAt: w.now.fixAt }, now);
    return { lon, lat, alt_ft: w.now.alt_ft, heading: w.now.heading, near: false };
  }

  function milesBetween(lat1, lon1, lat2, lon2) {
    const r = Math.PI / 180, dp = (lat2 - lat1) * r, dl = (lon2 - lon1) * r;
    const h = Math.sin(dp / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dl / 2) ** 2;
    return 3958.8 * 2 * Math.asin(Math.min(1, Math.sqrt(h)));
  }
  function bearingTo(lat1, lon1, lat2, lon2) {
    const r = Math.PI / 180, dl = (lon2 - lon1) * r;
    const y = Math.sin(dl) * Math.cos(lat2 * r);
    const x = Math.cos(lat1 * r) * Math.sin(lat2 * r) - Math.sin(lat1 * r) * Math.cos(lat2 * r) * Math.cos(dl);
    return (Math.atan2(y, x) / r + 360) % 360;
  }
  function fromHome(lat, lon) {
    const { home } = state.config;
    const miles = milesBetween(home.lat, home.lon, lat, lon);
    return `${miles < 100 ? miles.toFixed(1) : Math.round(miles).toLocaleString()} mi ${compass(bearingTo(home.lat, home.lon, lat, lon))}`;
  }
  const selKeyOf = (sel) => (sel ? `${sel.kind}:${sel.key}` : '');

  async function loadWhole() {
    const sel = state.selected;
    if (!sel) return;
    const key = selKeyOf(sel);
    let whole;
    try {
      if (sel.kind === 'history') {
        const data = await getJSON(`api/flight?id=${encodeURIComponent(sel.key)}`);
        whole = { status: data.whole_state, path: data.whole_path || [] };
      } else {
        const data = await getJSON(`api/trace?hex=${encodeURIComponent(sel.key)}`);
        whole = { status: 'so-far', path: data.path || [] };
      }
    } catch {
      whole = { status: 'error', path: [] };
    }
    if (selKeyOf(state.selected) !== key) return; // they picked something else meanwhile
    whole.key = key;
    whole.p3 = whole.path.map((p) => [p[1], p[0], (p[2] || 0) * FT]);
    whole.p2 = whole.path.map((p) => [p[1], p[0], 0]);
    state.whole = whole;
    renderDetail();
    render();
  }

  /** Zoom the map out (or in) to show a set of [lon, lat] points. */
  function fitTo(coords) {
    if (!coords.length) return;
    let lons = coords.map((c) => c[0]);
    if (Math.max(...lons) - Math.min(...lons) > 180) lons = lons.map((x) => (x < 0 ? x + 360 : x)); // across the Pacific
    const lats = coords.map((c) => c[1]);
    const sw = [Math.min(...lons), Math.min(...lats)];
    const ne = [Math.max(...lons), Math.max(...lats)];
    map.fitBounds([sw, ne], { padding: clearOfCards(sw, ne), maxZoom: 12.5, duration: 1300 });
  }

  /**
   * Padding that keeps a route clear of the cards floating over the map: either beside
   * them or above them, whichever leaves the route bigger on screen.
   */
  function clearOfCards(sw, ne) {
    const box = $('#map').getBoundingClientRect();
    const W = box.width, H = box.height, top = 70, edge = 40;
    const cards = ['#detail', '#heat-panel']
      .map((sel) => $(sel).getBoundingClientRect())
      .filter((r) => r.height > 0);

    let left = edge, right = edge, bottom = edge, sidewaysOk = true;
    for (const r of cards) {
      const x1 = r.left - box.left, x2 = r.right - box.left;
      if (x2 < W * 0.6) left = Math.max(left, x2 + 16);            // a card on the left
      else if (x1 > W * 0.4) right = Math.max(right, W - x1 + 16); // a card on the right
      else sidewaysOk = false;                                      // a full-width card (phones)
      bottom = Math.max(bottom, box.bottom - r.top + 16);
    }
    // How big the route would be with each choice (its shape at the current zoom is enough to compare).
    const a = map.project(sw), b = map.project(ne);
    const bw = Math.max(1, Math.abs(b.x - a.x)), bh = Math.max(1, Math.abs(b.y - a.y));
    const beside = sidewaysOk ? Math.min((W - left - right) / bw, (H - top - edge) / bh) : -1;
    const above = Math.min((W - 2 * edge) / bw, (H - top - bottom) / bh);
    const padding = beside >= above
      ? { top, bottom: edge, left, right }
      : { top, bottom, left: edge, right: edge };
    // Never ask for more padding than the map has room for.
    if (padding.top + padding.bottom > H - 60 || padding.left + padding.right > W - 60) {
      return { top: 40, bottom: 40, left: 30, right: 30 };
    }
    return padding;
  }

  function wholeNote() {
    const w = state.whole;
    const sel = state.selected;
    if (!sel) return '';
    if (!w) return '<div class="note">Getting its whole flight…</div>';
    const n = w.path.length;
    if (sel.kind === 'live') {
      return n > 1 ? '<div class="note">The pink line shows where it has flown so far.</div>' : '';
    }
    const all = state.wholeAll.byId.get(Number(sel.key));
    if (all && all.flying) {
      const pos = flyingNow(all, null, performance.now());
      return pos
        ? `<div class="note">✈ Still in the air: ${esc(fmtAlt(pos.alt_ft))}, ${esc(fromHome(pos.lat, pos.lon))} of home. The pink line runs from takeoff to where it is now.</div>`
        : '<div class="note">✈ Still in the air. The pink line shows its flight so far.</div>';
    }
    if (w.status === 'done' && n > 1) {
      return w.path[n - 1][2] === 0
        ? '<div class="note">Landed. The pink line is its whole flight, takeoff to landing.</div>'
        : '<div class="note">The pink line is its whole flight, as far as adsb.lol could follow it.</div>';
    }
    if (w.status === 'waiting' && n > 1) return '<div class="note">The pink line shows its flight so far; the rest is added automatically.</div>';
    if (w.status === 'waiting') return '<div class="note">Its whole flight is looked up from adsb.lol shortly after it lands.</div>';
    if (w.status === 'unavailable') return '<div class="note">adsb.lol has no record of the rest of this flight.</div>';
    return '';
  }

  function wholeButton() {
    const w = state.whole;
    if (!w || w.path.length < 2) return '';
    const text = state.selected.kind === 'live' ? 'Show where it’s come from'
      : w.status === 'done' ? 'Show its whole flight' : 'Show its flight so far';
    return `<button class="linkbtn" data-action="fit-whole">${text}</button>`;
  }

  function stat(k, v) {
    return `<div><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`;
  }

  let lastDetailHtml = '';
  function renderDetail() {
    const el = $('#detail');
    const thing = selectedThing();
    if (!thing) { el.hidden = true; lastDetailHtml = ''; return; }

    let html;
    if (thing.plane) {
      const p = thing.plane;
      const info = infoFor(p) || {};
      const photo = info.photo_url ? `<img class="photo" src="${esc(info.photo_url)}" alt="">` : '';
      html = `${photo}<button class="close" aria-label="Close">×</button><div class="body">
        <div class="title-row">${logo(p.callsign, true)}<div><h2>${esc(label(p))}</h2>
        <div class="airline">${esc(info.airline || info.owner || '')}</div></div></div>
        ${routeLong(info) ? `<div class="route-big">${esc(routeLong(info))}</div>` : ''}
        <div class="plane">${esc([info.aircraft_desc || p.type, p.registration].filter(Boolean).join(' · '))}</div>
        <div class="stats">
          ${stat('Altitude', fmtAlt(p.alt_ft))}${stat('Speed', fmtSpeed(p.speed_kt))}${stat('Heading', p.heading == null ? '—' : `${compass(p.heading)} ${Math.round(p.heading)}°`)}
          ${stat('From home', `${fmtDist(p.distance_miles)} ${compass(p.bearing)}`)}${stat('Type', p.type || '—')}${stat('Squawk', p.squawk || '—')}
        </div>
        ${thing.lost ? '<div class="note">Out of range now, showing its last position.</div>'
          : p.in_zone ? '<div class="note"><span class="badge">Overhead</span> inside your zone right now</div>' : ''}
        ${wholeNote()}
        <div class="links">${wholeButton()}<a href="https://globe.adsb.lol/?icao=${esc(p.hex)}" target="_blank" rel="noopener">Follow it on adsb.lol ↗</a></div>
      </div>`;
    } else {
      const f = thing.flight;
      const when = new Date(f.first_seen * 1000);
      const utcDay = when.toISOString().slice(0, 10);
      const photo = f.photo_url ? `<img class="photo" src="${esc(f.photo_url)}" alt="">` : '';
      const why = f.alerted ? 'You got an email about this one.'
        : f.quiet ? '🌙 Came over during quiet hours, so it went in the morning report.'
          : 'No alert was emailed for this one.';
      html = `${photo}<button class="close" aria-label="Close">×</button><div class="body">
        <div class="title-row">${logo(f.callsign, true)}<div><h2>${esc(label(f))}</h2>
        <div class="airline">${esc(f.airline || f.owner || '')}</div></div></div>
        ${routeLong(f) ? `<div class="route-big">${esc(routeLong(f))}</div>` : ''}
        <div class="plane">${esc([f.aircraft || f.type, f.registration].filter(Boolean).join(' · '))}</div>
        <div class="stats">
          ${stat(`Seen ${['Today', 'Yesterday'].includes(dayLabel(f.first_seen)) ? dayLabel(f.first_seen).toLowerCase() : dayLabel(f.first_seen)}`, fmtTime(f.first_seen))}${stat('Closest', `${fmtDist(f.closest_miles)} ${compass(f.bearing)}`)}${stat('Altitude', fmtAlt(f.closest_alt_ft))}
        </div>
        <div class="note">${esc(why)}</div>
        ${wholeNote()}
        <div class="links">${wholeButton()}<a href="https://globe.adsb.lol/?icao=${esc(f.hex)}&showTrace=${utcDay}" target="_blank" rel="noopener">Replay on adsb.lol ↗</a></div>
      </div>`;
    }
    if (html !== lastDetailHtml) {
      el.innerHTML = html;
      lastDetailHtml = html;
    }
    el.hidden = false;
  }

  // ---------- heat map of the busiest flight paths ----------
  const HEAT_COLORS = [[49, 54, 149], [69, 117, 180], [116, 173, 209], [254, 224, 144], [252, 141, 89], [215, 48, 39]];

  async function loadHeat() {
    if (!state.heat) return;
    try {
      state.heatData = await getJSON(`api/heat?days=${state.heatDays}`);
    } catch {
      if (!state.heatData) state.heatData = { cells: [], routes: [], max: 0 };
    }
    renderHeatPanel();
    render();
  }

  function renderHeatPanel() {
    const panel = $('#heat-panel');
    panel.hidden = !state.heat;
    $('#legend').hidden = state.heat;
    $('#btn-heat').setAttribute('aria-pressed', String(state.heat));
    if (!state.heat) return;
    $('#heat-days').value = String(state.heatDays);
    const data = state.heatData;
    const view = state.config ? Math.round(state.config.view_radius_miles) : 15;
    if (!data) {
      $('#heat-note').textContent = 'Loading…';
      $('#heat-routes').innerHTML = '';
      $('#heat-actions').innerHTML = '';
      return;
    }
    if (!data.cells.length) {
      $('#heat-note').textContent = 'No traffic recorded in this time yet. Keep planespotter running and the busy paths will light up.';
    } else {
      const since = data.first_day
        ? new Date(`${data.first_day}T12:00:00`).toLocaleDateString([], { day: 'numeric', month: 'short' }) : '';
      const zone = state.config ? state.config.radius_miles : 3;
      const whole = data.whole_flights || 0;
      const waiting = data.whole_flights_waiting || 0;
      $('#heat-note').textContent = `Within ${view} mi: every plane. Farther out: the whole flight of each plane`
        + ` that crossed your ${zone} mi zone, including ones still in the air (${whole.toLocaleString()} landed so far`
        + (waiting ? `, ${waiting.toLocaleString()} more being looked up)` : ')') + '.'
        + (since ? ` Recording since ${since}.` : '');
    }
    $('#heat-actions').innerHTML = (data.route_cells || []).length
      ? '<button class="linkbtn" data-action="fit-routes">See the whole routes ↗</button>'
        + '<button class="linkbtn" data-action="fit-home">Back to home</button>'
      : '';
    const routes = data.routes || [];
    $('#heat-routes').innerHTML = routes.length
      ? '<h3>Most common routes over your zone</h3>' + routes.map((r) => `
          <button data-route="${esc(`${r.origin} ${r.destination}`)}" title="Show these flights">
            <span class="r">${esc(r.origin)} → ${esc(r.destination)}</span>
            <span class="c">${esc([r.origin_city, r.destination_city].filter(Boolean).join(' → '))}</span>
            <span class="n">${r.flights}</span>
          </button>`).join('')
      : '';
  }

  function setHeat(on) {
    state.heat = on;
    saved.set('heat', on);
    renderHeatPanel();
    if (on) loadHeat(); else render();
  }

  /** Keep heat spots the size of their grid squares on screen as you zoom (~280 m near home, ~4.4 km far away). */
  function heatRadiusPixels(cellMetres, min, max) {
    const lat = state.config.home.lat * Math.PI / 180;
    const metresPerPixel = (MPP_Z0 * Math.cos(lat)) / 2 ** map.getZoom();
    // Three squares wide, so neighbouring squares blend into one smooth path.
    return Math.round(Math.min(max, Math.max(min, (cellMetres / metresPerPixel) * 3)) / 4) * 4;
  }

  // ---------- status pill ----------
  function updateStatus() {
    const el = $('#status');
    let text, cls = '';
    if (state.liveError === 'offline') { text = 'Can’t reach planespotter. Is it still running?'; cls = 'error'; }
    else if (state.liveError) { text = 'Live data is unavailable right now, retrying…'; cls = 'error'; }
    else if (state.liveMeta && state.liveMeta.paused > 60) {
      text = `adsb.lol’s daily limit reached · back in ~${Math.ceil(state.liveMeta.paused / 60)} min`; cls = 'error';
    }
    else if (state.liveMeta && state.liveMeta.waiting) { text = 'Waiting for the first check…'; }
    else {
      const n = state.live.length;
      const over = state.live.filter((p) => p.in_zone).length;
      text = `Live · ${n} plane${n === 1 ? '' : 's'} nearby${over ? ` · ${over} over your zone` : ''}`;
      const every = state.liveMeta ? state.liveMeta.interval : 0;
      if (every > 45) text += ` · updates every ${every >= 90 ? `${Math.round(every / 60)} min` : `${every} s`}`;
      if (state.quietNow) { text += ' · 🌙 quiet hours'; cls = 'quiet'; }
    }
    el.className = `status ${cls}`;
    $('#status-text').textContent = text;
  }

  // ---------- the map ----------
  let map, overlay;
  const PLANE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 64 64">'
    + '<path fill="#fff" d="M32 3c2.1 0 3.5 2.4 3.5 5.6v14.7l21.7 12.9v5.9L35.5 35.3v12.6l7.3 5.6v4.9L32 55.6l-10.8 2.8v-4.9l7.3-5.6V35.3L6.8 42.1v-5.9l21.7-12.9V8.6C28.5 5.4 29.9 3 32 3z"/></svg>';
  const PLANE_ICON = {
    iconAtlas: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(PLANE_SVG)}`,
    iconMapping: { plane: { x: 0, y: 0, width: 128, height: 128, mask: true } },
  };

  /** A zoom that fits your zone nicely: ~75% of the map in 2D, a bit more in tilted 3D. */
  function zoomFor(miles) {
    const el = $('#map');
    const px = Math.max(240, Math.min(el.clientWidth, el.clientHeight) || 600);
    const fill = state.mode === '3d' ? 0.98 : 0.75;
    const metresPerPixel = (2 * miles * MI) / (fill * px);
    return Math.log2((MPP_Z0 * Math.cos(state.config.home.lat * Math.PI / 180)) / metresPerPixel);
  }

  function addBuildings() {
    if (map.getLayer('pp-buildings-3d') || !map.getSource('openmaptiles')) return;
    const firstLabel = map.getStyle().layers.find((l) => l.type === 'symbol');
    map.addLayer({
      id: 'pp-buildings-3d', type: 'fill-extrusion', source: 'openmaptiles', 'source-layer': 'building', minzoom: 13,
      filter: ['!', ['has', 'hide_3d']],
      layout: { visibility: state.mode === '3d' ? 'visible' : 'none' },
      paint: {
        'fill-extrusion-color': dark ? '#1f2833' : '#d5dae1',
        'fill-extrusion-height': ['coalesce', ['get', 'render_height'], 0],
        'fill-extrusion-base': ['coalesce', ['get', 'render_min_height'], 0],
        'fill-extrusion-opacity': 0.85,
      },
    }, firstLabel && firstLabel.id);
  }

  function setMode(mode, animate = true) {
    state.mode = mode;
    saved.set('mode', mode);
    const is3d = mode === '3d';
    $('#btn-3d').setAttribute('aria-pressed', String(is3d));
    $('#btn-2d').setAttribute('aria-pressed', String(!is3d));
    if (is3d) {
      map.dragRotate.enable();
      map.touchPitch.enable();
      map.touchZoomRotate.enableRotation();
      map.easeTo({ pitch: 58, bearing: map.getBearing() || -18, duration: animate ? 900 : 0 });
    } else {
      map.dragRotate.disable();
      map.touchPitch.disable();
      map.touchZoomRotate.disableRotation();
      map.easeTo({ pitch: 0, bearing: 0, duration: animate ? 700 : 0 });
    }
    if (map.getLayer('pp-buildings-3d')) map.setLayoutProperty('pp-buildings-3d', 'visibility', is3d ? 'visible' : 'none');
    render();
  }

  function goHome() {
    const is3d = state.mode === '3d';
    map.flyTo({
      center: [state.config.home.lon, state.config.home.lat], zoom: zoomFor(state.config.radius_miles),
      pitch: is3d ? 58 : 0, bearing: is3d ? -18 : 0, duration: 1000, essential: true,
    });
  }

  let wholeMemo = { key: '' };
  const flightById = (id) => state.flights.find((f) => f.id === id);

  // A white tile with rounded corners, drawn behind each logo on the map.
  const TILE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect x="1" y="1" width="62" height="62" rx="14" fill="#fff" stroke="#d5dae1" stroke-width="2"/></svg>';
  const TILE = {
    iconAtlas: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(TILE_SVG)}`,
    iconMapping: { tile: { x: 0, y: 0, width: 64, height: 64 } },
  };

  /** Airline logos floating above labelled planes (only logos we know exist, so nothing fails to load). */
  function logoLayers(items) {
    const data = items.filter((d) => d.code && state.logoOK.has(d.code));
    if (!data.length) return [];
    return [
      new deck.IconLayer({
        id: 'logo-tiles', data, ...TILE, getIcon: () => 'tile', getPosition: (d) => d.pos,
        getSize: 26, sizeUnits: 'pixels', getPixelOffset: [0, -50],
      }),
      new deck.IconLayer({
        id: 'logos', data, getPosition: (d) => d.pos, getSize: 20, sizeUnits: 'pixels', getPixelOffset: [0, -50],
        getIcon: (d) => ({ url: `logo/${d.code}.png`, width: 90, height: 90, id: d.code }),
      }),
    ];
  }

  /** Rebuilds the deck.gl layers. Called ~20 times a second so planes glide smoothly. */
  function render() {
    if (!overlay || !state.config) return;
    const is3d = state.mode === '3d';
    const z = (ft) => (is3d && ft ? ft * FT : 0);
    const now = performance.now();
    const sel = state.selected;
    const selKey = sel ? `${sel.kind}:${sel.key}` : '';
    const { home, radius_miles: radius, max_altitude_ft: ceiling } = state.config;

    const planes = state.live.map((p) => {
      const [lon, lat] = livePos(p, now);
      return { p, lon, lat, z: z(p.alt_ft), selected: isSelected('live', p.hex) };
    });
    const planeColor = (d) => (d.selected ? C.select : d.p.in_zone ? C.accent : C.blue);
    const showHistory = state.tab === 'history' || (sel && sel.kind === 'history');
    const history = showHistory ? state.historyView.slice(0, 1500) : [];
    // Label planes near home only when zoomed in enough to tell them apart (the selected one always).
    const zoomedIn = map.getZoom() >= 8;
    const labelled = planes.filter((d) => d.selected || (zoomedIn && (d.p.in_zone || planes.length <= 25)));
    const W = state.wholeAll;
    const planesByHex = new Map(planes.map((d) => [d.p.hex, d]));
    // Whole flights to draw: on Spotted, every flight in the list; on In the air, the ones still flying.
    const memoKey = `${W.version}|${state.historyVersion}|${state.tab}|${showHistory}`;
    if (wholeMemo.key !== memoKey) {
      const ids = state.tab === 'history' ? new Set(state.historyView.map((f) => f.id)) : null;
      const landed = [], flying = [];
      for (const w of W.flights) {
        if (ids ? !ids.has(w.id) : !w.flying) continue;
        (w.flying ? flying : landed).push(w);
      }
      wholeMemo = {
        key: memoKey, landed, flying,
        // Flights without a whole path yet still get the bit we saw near home.
        localOnly: history.filter((f) => f.path.length > 1 && !W.byId.has(f.id)),
      };
    }
    const flyingNowList = wholeMemo.flying.map((w) => {
      const pos = flyingNow(w, planesByHex, now);
      const base = is3d ? w.p3 : w.p2;
      return { w, pos, path: pos ? [...base, [pos.lon, pos.lat, z(pos.alt_ft)]] : base };
    });
    const farAway = flyingNowList.filter((d) => d.pos && !d.pos.near); // not on the live feed: draw a plane
    const historyWithPaths = wholeMemo.localOnly;
    const selFlight = sel && sel.kind === 'history' ? state.flights.find((f) => String(f.id) === sel.key) : null;
    // The selected flight's whole path (for a live plane: from takeoff up to where it is now).
    const whole = state.whole && state.whole.key === selKey && state.whole.path.length > 1 ? state.whole : null;
    let wholePath = whole ? (is3d ? whole.p3 : whole.p2) : null;
    const selPlane = whole && sel.kind === 'live' ? planes.find((d) => d.selected) : null;
    if (wholePath && selPlane) wholePath = [...wholePath, [selPlane.lon, selPlane.lat, selPlane.z]];
    if (wholePath && sel.kind === 'history') {
      const w = W.byId.get(Number(sel.key));
      const pos = w && w.flying ? flyingNow(w, planesByHex, now) : null;
      if (pos) wholePath = [...wholePath, [pos.lon, pos.lat, z(pos.alt_ft)]];
    }

    const layers = [
      new deck.PolygonLayer({
        id: 'zone', data: [{ polygon: state.zone }], getPolygon: (d) => d.polygon,
        filled: true, stroked: true, getFillColor: a(C.accent, dark ? 20 : 26), getLineColor: a(C.accent, 230),
        lineWidthUnits: 'pixels', getLineWidth: 2, parameters: GROUND,
      }),
      state.heat && state.heatData && (state.heatData.route_cells || []).length ? new deck.HeatmapLayer({
        id: 'heat-routes', data: state.heatData.route_cells, getPosition: (c) => [c[0], c[1]], getWeight: (c) => c[2],
        radiusPixels: heatRadiusPixels(4440, 12, 140), intensity: 1.3, threshold: 0.03, opacity: dark ? 0.8 : 0.65,
        colorRange: HEAT_COLORS, aggregation: 'SUM', parameters: GROUND,
      }) : null,
      state.heat && state.heatData && state.heatData.cells.length ? new deck.HeatmapLayer({
        id: 'heat', data: state.heatData.cells, getPosition: (c) => [c[0], c[1]], getWeight: (c) => c[2],
        radiusPixels: heatRadiusPixels(280, 16, 160), intensity: 1.3, threshold: 0.03, opacity: dark ? 0.8 : 0.65,
        colorRange: HEAT_COLORS, aggregation: 'SUM', parameters: GROUND,
      }) : null,
      is3d && ceiling ? new deck.PolygonLayer({
        id: 'zone-ceiling', data: [{ polygon: state.zone }], getPolygon: (d) => d.polygon,
        extruded: true, wireframe: true, getElevation: ceiling * FT, material: false,
        getFillColor: a(C.accent, 16), getLineColor: a(C.accent, 90),
      }) : null,
      new deck.TextLayer({
        id: 'zone-label', data: [{ pos: destination(home.lat, home.lon, 0, radius * MI), text: `${radius} mi` }],
        getPosition: (d) => d.pos, getText: (d) => d.text, getSize: 12, getColor: a(C.accent, 255),
        fontWeight: 700, getPixelOffset: [0, -10], background: true, getBackgroundColor: a(C.panel, 210),
        backgroundPadding: [5, 2], fontFamily: '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif',
      }),

      // Whole flights of planes that came over: takeoff to landing, or to where they are now.
      new deck.PathLayer({
        id: 'whole-landed', data: wholeMemo.landed, pickable: true,
        getPath: (w) => (is3d ? w.p3 : w.p2), getColor: a(C.accent, dark ? 95 : 120),
        getWidth: 2, widthUnits: 'pixels', capRounded: true, jointRounded: true,
        updateTriggers: { getPath: is3d },
      }),
      new deck.PathLayer({
        id: 'whole-flying', data: flyingNowList, pickable: true,
        getPath: (d) => d.path, getColor: a(C.accent, 215),
        getWidth: 2.5, widthUnits: 'pixels', capRounded: true, jointRounded: true,
      }),
      new deck.IconLayer({
        id: 'flying-planes', data: farAway, pickable: true, ...PLANE_ICON, getIcon: () => 'plane',
        getPosition: (d) => [d.pos.lon, d.pos.lat, z(d.pos.alt_ft)], getAngle: (d) => -(d.pos.heading || 0),
        getSize: 26, sizeUnits: 'pixels', billboard: false,
        getColor: (d) => a(selKey === `history:${d.w.id}` ? C.select : C.accent, 255),
        updateTriggers: { getColor: selKey },
      }),
      new deck.TextLayer({
        id: 'flying-labels', data: farAway, pickable: true,
        getPosition: (d) => [d.pos.lon, d.pos.lat, z(d.pos.alt_ft)],
        getText: (d) => { const f = flightById(d.w.id); return f ? label(f) : ''; },
        getPixelOffset: [0, -24], getSize: 11, getColor: a(C.ink, 255), fontWeight: 600,
        background: true, getBackgroundColor: a(C.panel, 200), backgroundPadding: [5, 2],
        fontFamily: '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif',
        updateTriggers: { getText: state.historyVersion },
      }),

      // History: the paths of planes that came over, and where each came closest.
      new deck.PathLayer({
        id: 'history-paths', data: historyWithPaths, pickable: true,
        getPath: (f) => (is3d ? f.path3d : f.path2d),
        getColor: (f) => (selKey === `history:${f.id}` ? a(C.select, 255) : a(C.accent, 85)),
        getWidth: (f) => (selKey === `history:${f.id}` ? 5 : 2), widthUnits: 'pixels', capRounded: true, jointRounded: true,
        updateTriggers: { getPath: is3d, getColor: selKey, getWidth: selKey },
      }),
      new deck.ScatterplotLayer({
        id: 'history-points', data: history, pickable: true,
        getPosition: (f) => [f.lon, f.lat, z(f.closest_alt_ft)],
        getRadius: (f) => (selKey === `history:${f.id}` ? 7 : 3.5), radiusUnits: 'pixels',
        getFillColor: (f) => (selKey === `history:${f.id}` ? a(C.select, 255) : a(C.accent, 200)),
        stroked: true, getLineColor: a(C.panel, 255), lineWidthUnits: 'pixels', getLineWidth: 1,
        updateTriggers: { getPosition: is3d, getRadius: selKey, getFillColor: selKey },
      }),
      wholePath ? new deck.PathLayer({
        id: 'whole-flight', data: [{ path: wholePath }], getPath: (d) => d.path,
        getColor: a(C.select, 220), getWidth: 3, widthUnits: 'pixels', capRounded: true, jointRounded: true,
      }) : null,
      selFlight && is3d && selFlight.closest_alt_ft ? new deck.LineLayer({
        id: 'history-drop', data: [selFlight],
        getSourcePosition: (f) => [f.lon, f.lat, 0], getTargetPosition: (f) => [f.lon, f.lat, z(f.closest_alt_ft)],
        getColor: a(C.select, 200), getWidth: 1.5, widthUnits: 'pixels',
      }) : null,
      selFlight ? new deck.TextLayer({
        id: 'history-label', data: [selFlight], getText: (f) => `${label(f)}  ${fmtAlt(f.closest_alt_ft)}`,
        getPosition: (f) => [f.lon, f.lat, z(f.closest_alt_ft)], getPixelOffset: [0, -20], getSize: 12,
        getColor: a(C.ink, 255), background: true, getBackgroundColor: a(C.panel, 230), backgroundPadding: [6, 3],
        fontWeight: 600, fontFamily: '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif',
      }) : null,

      // Home.
      new deck.ScatterplotLayer({
        id: 'home', data: [home], getPosition: (h) => [h.lon, h.lat], getRadius: 6, radiusUnits: 'pixels',
        getFillColor: a(C.ink, 255), stroked: true, getLineColor: a(C.accent, 255), lineWidthUnits: 'pixels', getLineWidth: 3,
      }),
      new deck.TextLayer({
        id: 'home-label', data: [home], getPosition: (h) => [h.lon, h.lat], getText: (h) => h.name,
        getSize: 12, getColor: a(C.ink, 255), getPixelOffset: [0, 16], fontWeight: 700,
        background: true, getBackgroundColor: a(C.panel, 200), backgroundPadding: [5, 2],
        fontFamily: '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif',
      }),

      // Live planes: trails, drop lines to the ground, shadows, the planes, labels.
      new deck.PathLayer({
        id: 'trails', data: planes.filter((d) => state.trails.has(d.p.hex)),
        getPath: (d) => [...state.trails.get(d.p.hex).map((t) => [t[0], t[1], is3d ? t[2] : 0]), [d.lon, d.lat, d.z]],
        getColor: (d) => a(planeColor(d), d.selected ? 230 : d.p.in_zone ? 170 : 110),
        getWidth: (d) => (d.selected ? 3 : 2), widthUnits: 'pixels', jointRounded: true, capRounded: true,
      }),
      is3d ? new deck.LineLayer({
        id: 'drops', data: planes, getSourcePosition: (d) => [d.lon, d.lat, 0], getTargetPosition: (d) => [d.lon, d.lat, d.z],
        getColor: (d) => a(planeColor(d), d.selected ? 200 : 70), getWidth: 1, widthUnits: 'pixels',
      }) : null,
      is3d ? new deck.ScatterplotLayer({
        id: 'shadows', data: planes, getPosition: (d) => [d.lon, d.lat, 0], getRadius: 3, radiusUnits: 'pixels',
        getFillColor: dark ? [0, 0, 0, 150] : [20, 30, 40, 70],
      }) : null,
      new deck.IconLayer({
        id: 'planes', data: planes, pickable: true, ...PLANE_ICON, getIcon: () => 'plane',
        getPosition: (d) => [d.lon, d.lat, d.z], getAngle: (d) => -(d.p.heading || 0),
        getSize: (d) => (d.selected ? 38 : d.p.in_zone ? 32 : 26), sizeUnits: 'pixels', billboard: false,
        getColor: (d) => a(planeColor(d), 255),
      }),
      new deck.TextLayer({
        id: 'plane-labels', data: labelled,
        getPosition: (d) => [d.lon, d.lat, d.z], getText: (d) => label(d.p), getPixelOffset: [0, -24],
        getSize: (d) => (d.selected ? 13 : 11), getColor: a(C.ink, 255), fontWeight: 600,
        background: true, getBackgroundColor: (d) => (d.selected ? a(C.select, 90) : a(C.panel, 200)), backgroundPadding: [5, 2],
        fontFamily: '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif',
      }),
      ...logoLayers(labelled.map((d) => ({ code: airlineCode(d.p.callsign), pos: [d.lon, d.lat, d.z] }))
        .concat(farAway.map((d) => {
          const f = flightById(d.w.id);
          return { code: f && airlineCode(f.callsign), pos: [d.pos.lon, d.pos.lat, z(d.pos.alt_ft)] };
        }))),
    ].filter(Boolean);

    overlay.setProps({ layers });
  }

  let lastFrame = 0;
  function animate(ts) {
    if (!document.hidden && ts - lastFrame > 50) { render(); lastFrame = ts; }
    requestAnimationFrame(animate);
  }

  function onMapClick(e) {
    const hit = overlay.pickObject({ x: e.point.x, y: e.point.y, radius: 8 });
    if (!hit || !hit.object) { if (state.selected) select(null); return; }
    const id = hit.layer.id;
    if (id === 'planes' || id === 'plane-labels' || id === 'trails') select('live', hit.object.p.hex, { fly: false, scroll: true });
    else if (id.startsWith('history') || id === 'whole-landed') select('history', hit.object.id, { fly: false, scroll: true });
    else if (id === 'whole-flying' || id.startsWith('flying-')) select('history', hit.object.w.id, { fly: false, scroll: true });
  }

  // ---------- wiring ----------
  function setTab(tab) {
    state.tab = tab;
    saved.set('tab', tab);
    for (const b of document.querySelectorAll('.tabs button')) b.setAttribute('aria-selected', String(b.dataset.tab === tab));
    $('#history-filters').hidden = tab !== 'history';
    state.listLimit = LIST_PAGE;
    $('#list').scrollTop = 0;
    renderList();
    if (tab === 'history') loadFlights();
    loadWholeAll();
  }

  function wireUp() {
    for (const b of document.querySelectorAll('.tabs button')) b.addEventListener('click', () => setTab(b.dataset.tab));
    $('#search').addEventListener('input', (e) => {
      state.query = e.target.value.trim().toLowerCase();
      state.listLimit = LIST_PAGE;
      renderList();
    });
    for (const chip of document.querySelectorAll('[data-days]')) {
      chip.setAttribute('aria-pressed', String(Number(chip.dataset.days) === state.days));
      chip.addEventListener('click', () => {
        state.days = Number(chip.dataset.days);
        setTimeout(loadWholeAll, 0);
        saved.set('days', state.days);
        for (const c of document.querySelectorAll('[data-days]')) c.setAttribute('aria-pressed', String(c === chip));
        loadFlights();
      });
    }
    $('#sort').value = state.sort;
    $('#sort').addEventListener('change', (e) => { state.sort = e.target.value; saved.set('sort', state.sort); renderList(); });
    $('#list').addEventListener('click', (e) => {
      if (e.target.closest('[data-action="more"]')) { state.listLimit += LIST_PAGE; renderList(); return; }
      const item = e.target.closest('.item');
      if (item) select(item.dataset.kind, item.dataset.key);
    });
    $('#detail').addEventListener('click', (e) => {
      if (e.target.closest('.close')) select(null);
      if (e.target.closest('[data-action="fit-whole"]') && state.whole) {
        const coords = state.whole.p2.slice();
        const plane = state.selected && state.selected.kind === 'live' && selectedThing();
        if (plane && plane.plane) coords.push(livePos(plane.plane, performance.now()));
        fitTo(coords);
      }
    });
    $('#heat-actions').addEventListener('click', (e) => {
      if (e.target.closest('[data-action="fit-home"]')) { goHome(); return; }
      if (!e.target.closest('[data-action="fit-routes"]') || !state.heatData) return;
      // The middle 90% of the route heat, so one long-haul flight doesn't zoom out to the whole world.
      const cells = state.heatData.route_cells;
      const pick = (arr, q) => arr[Math.min(arr.length - 1, Math.floor(q * arr.length))];
      const lons = cells.map((c) => c[0]).sort((x, y) => x - y);
      const lats = cells.map((c) => c[1]).sort((x, y) => x - y);
      const { home } = state.config;
      fitTo([[pick(lons, 0.05), pick(lats, 0.05)], [pick(lons, 0.95), pick(lats, 0.95)], [home.lon, home.lat]]);
    });
    $('#btn-3d').addEventListener('click', () => setMode('3d'));
    $('#btn-2d').addEventListener('click', () => setMode('2d'));
    $('#btn-home').addEventListener('click', goHome);
    $('#btn-heat').addEventListener('click', () => setHeat(!state.heat));
    $('#heat-days').addEventListener('change', (e) => {
      state.heatDays = Number(e.target.value);
      saved.set('heatDays', state.heatDays);
      state.heatData = null;
      renderHeatPanel();
      loadHeat();
    });
    $('#heat-routes').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-route]');
      if (!btn) return;
      $('#search').value = btn.dataset.route;
      state.query = btn.dataset.route.toLowerCase();
      setTab('history');
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && state.selected) select(null); });
  }

  async function start() {
    wireUp();
    try {
      state.config = await getJSON('api/config');
    } catch {
      state.liveError = 'offline';
      updateStatus();
      $('#home-line').textContent = 'Not connected';
      return;
    }
    const { home, radius_miles: radius, max_altitude_ft: ceiling } = state.config;
    $('#home-line').textContent = `${home.name} · ${radius} mi zone${ceiling ? `, below ${ceiling.toLocaleString()} ft` : ''}`;
    document.title = `planespotter · ${home.name}`;
    state.zone = circle(home.lat, home.lon, radius);

    const is3d = state.mode === '3d';
    map = new maplibregl.Map({
      container: 'map', style: dark ? STYLE.dark : STYLE.light,
      center: [home.lon, home.lat], zoom: zoomFor(radius),
      pitch: is3d ? 58 : 0, bearing: is3d ? -18 : 0, maxPitch: 80,
      antialias: true, attributionControl: false,
    });
    map.addControl(new maplibregl.AttributionControl({
      compact: true, customAttribution: 'Flights: <a href="https://adsb.lol" target="_blank">adsb.lol</a> (ODbL)',
    }), 'bottom-right');
    overlay = new deck.MapboxOverlay({ interleaved: true, layers: [] });
    map.addControl(overlay);
    map.on('style.load', addBuildings);
    map.on('styleimagemissing', (e) => {
      if (!map.hasImage(e.id)) map.addImage(e.id, { width: 1, height: 1, data: new Uint8Array(4) });
    });
    map.on('load', () => { addBuildings(); setMode(state.mode, false); });
    map.on('click', onMapClick);

    setTab(state.tab);
    pollLive();
    if (state.tab !== 'history') loadFlights();
    setInterval(() => { if (!document.hidden) loadWholeAll(); }, 30000);
    setInterval(() => { if (!document.hidden) loadFlights(); }, HISTORY_EVERY_MS);
    renderHeatPanel();
    loadHeat();
    setInterval(() => { if (!document.hidden) loadHeat(); }, HISTORY_EVERY_MS);
    // Keep the selected flight's whole path up to date (a live plane keeps flying).
    setInterval(() => { if (!document.hidden && state.selected) loadWhole(); }, HISTORY_EVERY_MS);
    requestAnimationFrame(animate);
  }

  start();
})();

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
    replay: { on: false, playing: false, t: 0, speed: 300, flights: [], day: '' },
    chime: saved.get('chime', false),
    chimed: new Map(),          // hex -> when we last chimed for it
    wholeAll: { flights: [], byId: new Map(), version: 0 },  // every spotted flight's whole path
    logoOK: new Set(),          // airline codes whose logo loaded
    logoBad: new Set(),         // ...and ones that have no logo
    voice: saved.get('voice', false),
    spoken: new Map(),          // hex -> when we last announced it
    sky: null,                  // clouds near home + where the sun is (api/sky)
    yours: { photos: [], eyes: [], streak: null },  // your photos and "I saw it" planes
    noise: null,                // the noise tab (api/noise)
    noiseDays: saved.get('noiseDays', 30),
    flow: null,                 // which way the airport is landing (api/flow)
    today: null,                // your regular flights today: on time, late... (api/today)
    space: null,                // space station passes and tracks (api/space)
    quiz: null,                 // the question you're answering
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
                           waiting: !!data.waiting, receiver: data.source === 'receiver' };
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
      selectPending();
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
    selectPending();
  }

  /** Select the flight a notification link pointed at, as soon as it has loaded. */
  function selectPending() {
    const want = state.pendingSelect;
    if (!want || !map) return;
    const found = want.kind === 'live' ? state.live.some((p) => p.hex === want.key)
      : state.flights.some((f) => String(f.id) === want.key);
    if (!found) return;
    state.pendingSelect = null;
    history.replaceState(null, '', location.pathname);
    select(want.kind, want.key, { scroll: true });
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

  // ---------- your collection ----------
  async function loadCollection() {
    try { state.collection = await getJSON('api/collection'); } catch { /* keep what we had */ }
    if (state.tab === 'collection') renderList();
  }

  /**
   * A single-series column chart as inline SVG: thin columns (max 24 px) with 4 px rounded
   * tops and square bottoms on a hairline baseline, a 2 px gap between them, and a tooltip
   * on hover. One series, so no legend: the heading says what it shows.
   */
  function columnChart(values, tips, ticks) {
    const W = 330, H = 120, top = 6, bottom = 20, plot = H - top - bottom;
    const slot = W / values.length;
    const bw = Math.min(24, slot - 2);
    const max = Math.max(1, ...values);
    let bars = '';
    values.forEach((v, i) => {
      const x = i * slot + (slot - bw) / 2;
      const h = v ? Math.max(2, (v / max) * plot) : 0;
      const y = top + plot - h;
      const r = Math.min(4, h, bw / 2);
      const shape = h ? `<path class="col" d="M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + bw - r}Q${x + bw},${y} ${x + bw},${y + r}V${y + h}Z"/>` : '';
      // A full-height hit area, bigger than the column, for the tooltip.
      bars += `<g class="slot" data-tip="${esc(tips[i])}">${shape}<rect class="hit" x="${i * slot}" y="${top}" width="${slot}" height="${plot}"/></g>`;
    });
    const labels = ticks.map(([i, text]) => `<text class="tick" x="${i * slot + slot / 2}" y="${H - 5}">${esc(text)}</text>`).join('');
    return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img">`
      + `<line class="base" x1="0" x2="${W}" y1="${top + plot}" y2="${top + plot}"/>${bars}${labels}</svg>`
      + '<div class="chart-tip" hidden></div></div>';
  }

  function collectionHtml() {
    const c = state.collection;
    if (!c) return empty('Loading your collection…');
    if (!c.totals.planes) return empty('Nothing spotted yet. Keep planespotter running and your collection will grow.');
    const t = c.totals;
    const since = new Date(c.since * 1000).toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' });
    const tile = (k, v) => `<div class="ctile"><div class="k">${esc(k)}</div><div class="v">${Number(v).toLocaleString()}</div></div>`;
    const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
    const hourTips = c.hours.map((n, h) => `${String(h).padStart(2, '0')}:00–${String((h + 1) % 24).padStart(2, '0')}:00 · ${plural(n, 'plane')}`);
    const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    const dayTips = c.weekdays.map((n, i) => `${days[i]} · ${plural(n, 'plane')}`);
    const earned = c.badges.filter((b) => b.earned_at).length;
    const badge = (b) => `<div class="cbadge${b.earned_at ? ' earned' : ''}" title="${esc(b.how)}">
        <div class="emoji">${b.emoji}</div><div class="name">${esc(b.name)}</div>
        <div class="how">${b.earned_at ? esc(new Date(b.earned_at * 1000).toLocaleDateString([], { day: 'numeric', month: 'short' }))
          : esc(b.progress || b.how)}</div></div>`;
    const showAll = state.collectionAll || {};
    const listOf = (key, rows, row) => {
      const limit = showAll[key] ? rows.length : 8;
      return rows.slice(0, limit).map(row).join('')
        + (rows.length > limit ? `<button class="chip more" data-show-all="${key}">Show all ${rows.length}</button>` : '');
    };
    const extra = extrasHtml(c);
    return `<div class="collection">
      <p class="csub">Everything you've spotted since ${esc(since)}.</p>
      ${extra.top}
      <div class="ctiles">${tile('Planes', t.planes)}${tile('Airlines', t.airlines)}${tile('Aircraft types', t.types)}${tile('Countries', t.countries)}</div>
      <h3>Badges <span>${earned} of ${c.badges.length}</span></h3>
      <div class="cbadges">${c.badges.map(badge).join('')}</div>
      ${extra.records}
      ${extra.photos}
      <h3>Planes by time of day</h3>
      ${columnChart(c.hours, hourTips, [[0, '00:00'], [6, '06:00'], [12, '12:00'], [18, '18:00']])}
      <h3>Planes by day of the week</h3>
      ${columnChart(c.weekdays, dayTips, days.map((d, i) => [i, d]))}
      <h3>Airlines <span>${t.airlines}</span></h3>
      ${listOf('airlines', c.airlines, (a) => `<div class="crow">${logo(`${a.code}1`)}<span class="cname">${esc(a.name)}</span><span class="cnum">${a.count}</span></div>`)}
      <h3>Aircraft types <span>${t.types}</span></h3>
      ${listOf('types', c.types, (ty) => `<div class="crow"><span class="ccode">${esc(ty.code)}</span><span class="cname">${esc(ty.name)}</span><span class="cnum">${ty.count}</span></div>`)}
      <h3>Countries <span>${t.countries}</span></h3>
      ${listOf('countries', c.countries, (co) => `<div class="crow"><span class="cflag">${co.flag}</span><span class="cname">${esc(co.name)}</span><span class="cnum">${co.count}</span></div>`)}
    </div>`;
  }

  // ---------- the list ----------
  /** The route we checked against the plane's real flight, if it went over us in the last few hours. */
  function checkedRoute(hex) {
    const f = state.flights.find((x) => x.hex === hex && x.route_checked && Date.now() / 1000 - x.first_seen < 6 * 3600);
    return f ? { origin: f.origin, destination: f.destination, origin_city: f.origin_city, destination_city: f.destination_city } : null;
  }

  /** "✨ A380", "🚨 7700"... for special planes. */
  const specialBadges = (p) => (p.special || []).map((x) =>
    `<span class="badge ${x.emergency ? 'sos' : 'special'}" title="${esc(x.text)}">${x.emergency ? '🚨' : '✨'} ${esc(x.short)}</span>`).join('');

  function liveItem(p) {
    const info = { ...(infoFor(p) || {}), ...(checkedRoute(p.hex) || {}) };
    const selected = isSelected('live', p.hex) ? ' selected' : '';
    const sub = [info.airline, info.aircraft_desc || p.type].filter(Boolean).join(' · ');
    return `<button class="item${selected}" data-kind="live" data-key="${esc(p.hex)}" role="listitem">
      ${logo(p.callsign)}
      <div class="main">
        <div class="title"><span class="callsign">${esc(label(p))}</span>${p.in_zone ? '<span class="badge">Overhead</span>' : ''}${specialBadges(p)}<span class="route">${esc(routeShort(info))}</span></div>
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
      <div class="time">${time}${f.quiet ? '<br>🌙' : ''}${f.offline ? '<br><span title="Found afterwards: planespotter wasn’t running then">📴</span>' : ''}</div>
      ${logo(f.callsign)}
      <div class="main">
        <div class="title"><span class="callsign">${esc(label(f))}</span>${isFlying(f.id) ? '<span class="badge air">In the air</span>' : ''}${delayBadge(f.delay)}<span class="route">${esc(routeShort(f))}</span></div>
        <div class="sub">${esc(sub) || '&nbsp;'}</div>
      </div>
      <div class="meta"><div class="alt">${fmtAlt(f.closest_alt_ft)}</div><div class="dist">${fmtDist(f.closest_miles)} ${compass(f.bearing)}</div></div>
    </button>`;
  }

  /** "12 min late" / "on time" for your regular flights. */
  const delayBadge = (d) => (d == null ? '' : Math.abs(d) <= 10 ? '<span class="badge ok">on time</span>'
    : `<span class="badge ${d > 0 ? 'late' : 'ok'}">${Math.abs(d)} min ${d > 0 ? 'late' : 'early'}</span>`);

  const empty = (text) => `<div class="empty">${text}</div>`;
  const moreButton = (shown, total) =>
    total > shown ? `<button class="chip more" data-action="more">Show more (${(total - shown).toLocaleString()} left)</button>` : '';

  function renderList() {
    const el = $('#list');
    $('#live-count').textContent = state.live.length;
    if (state.tab === 'collection' || state.tab === 'noise') {
      el.innerHTML = state.tab === 'collection' ? collectionHtml() : noiseHtml();
      render();
      return;
    }
    if (state.tab === 'live') {
      const items = state.live.filter((p) => matches(p, infoFor(p)));
      // Planes that went over you and are still flying, but are now too far away for the live list.
      const nearby = new Set(state.live.map((p) => p.hex));
      const away = state.wholeAll.flights
        .filter((w) => w.flying && !nearby.has(w.hex))
        .map((w) => ({ w, f: flightById(w.id) }))
        .filter(({ f }) => f && matches(f));
      const extra = state.query ? '' : extraLiveSections();
      if (!items.length && !away.length) {
        el.innerHTML = empty(state.query ? 'No planes in the air match your search.'
          : state.liveError ? 'Waiting for live data…' : 'No planes nearby right now.') + extra;
        render();
        return;
      }
      el.innerHTML = items.slice(0, state.listLimit).map(liveItem).join('') + moreButton(state.listLimit, items.length)
        + (away.length ? `<div class="list-section">Went over you · still flying</div>${away.map(awayItem).join('')}` : '')
        + extra;
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
        whole = { status: 'so-far', path: data.path || [], origin: data.origin || null };
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
      const from = w.origin ? `Took off from ${esc(place(w.origin.code, w.origin.city || w.origin.name))}. ` : '';
      return n > 1 ? `<div class="note">${from}The pink line shows where it has flown so far.</div>` : '';
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

  /** Your own photo if you've added one (tap it to see it big), otherwise one from the aircraft database. */
  function photoHtml(thing, fallbackUrl) {
    const mine = photosOf(thing);
    if (!mine.length) return fallbackUrl ? `<img class="photo" src="${esc(fallbackUrl)}" alt="">` : '';
    const more = mine.length > 1 ? `<span class="photo-count">📸 ${mine.length}</span>` : '<span class="photo-count">📸 yours</span>';
    return `<button class="photo-btn" data-view-photo="${mine[0].id}"><img class="photo" src="photo/${mine[0].id}-thumb.jpg" alt="Your photo">${more}</button>`;
  }

  function actionsHtml(thing) {
    const hex = thing.plane ? thing.plane.hex : thing.flight.hex;
    const at = thing.plane ? Date.now() / 1000 : thing.flight.first_seen;
    const recent = Date.now() / 1000 - at < 7 * 86400;
    const seen = sawIt(hex, at);
    const flying = thing.plane ? !thing.lost : isFlying(thing.flight.id);
    const known = thing.flight || state.flights.some((f) => f.hex === hex);
    return `<div class="actions">
      ${recent ? `<button class="act${seen ? ' done' : ''}" data-action="saw" data-at="${at}">${seen ? '✓ You saw it' : '👀 I saw it'}</button>` : ''}
      ${flying ? `<button class="act${isFollowing(hex) ? ' done' : ''}" data-action="follow" title="A notification on your phone when it lands">${isFollowing(hex) ? '🔔 Following' : '🔔 Follow'}</button>` : ''}
      <button class="act" data-action="add-photo">📷 Add photo</button>
      <button class="act" data-action="share">↗ Share</button>
      ${known ? '<button class="act" data-action="profile">✈ This plane</button>' : ''}</div>`;
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
      html = `${photoHtml(thing, info.photo_url)}<button class="close" aria-label="Close">×</button><div class="body">
        <div class="title-row">${logo(p.callsign, true)}<div><h2>${esc(label(p))}</h2>
        <div class="airline">${esc(info.airline || info.owner || '')}</div></div></div>
        ${routeLong({ ...info, ...(checkedRoute(p.hex) || {}) }) ? `<div class="route-big">${esc(routeLong({ ...info, ...(checkedRoute(p.hex) || {}) }))}</div>` : ''}
        <div class="plane">${esc([info.aircraft_desc || p.type, p.registration].filter(Boolean).join(' · '))}</div>
        <div class="stats">
          ${stat('Altitude', fmtAlt(p.alt_ft))}${stat('Speed', fmtSpeed(p.speed_kt))}${stat('Heading', p.heading == null ? '—' : `${compass(p.heading)} ${Math.round(p.heading)}°`)}
          ${stat('From home', `${fmtDist(p.distance_miles)} ${compass(p.bearing)}`)}${stat('Type', p.type || '—')}${stat('Squawk', p.squawk || '—')}
        </div>
        ${(p.special || []).map((x) => `<div class="note"><strong>${x.emergency ? '🚨' : '✨'} ${esc(x.text)}</strong></div>`).join('')}
        ${thing.lost ? '<div class="note">Out of range now, showing its last position.</div>'
          : p.in_zone ? '<div class="note"><span class="badge">Overhead</span> inside your zone right now</div>' : ''}
        ${wholeNote()}
        ${actionsHtml(thing)}
        <div class="links">${wholeButton()}<a href="https://globe.adsb.lol/?icao=${esc(p.hex)}" target="_blank" rel="noopener">Follow it on adsb.lol ↗</a></div>
      </div>`;
    } else {
      const f = thing.flight;
      const when = new Date(f.first_seen * 1000);
      const utcDay = when.toISOString().slice(0, 10);
      const why = f.alerted ? 'You got an email about this one.'
        : f.quiet ? '🌙 Came over during quiet hours, so it went in the morning report.'
          : 'No alert was emailed for this one.';
      html = `${photoHtml(thing, f.photo_url)}<button class="close" aria-label="Close">×</button><div class="body">
        <div class="title-row">${logo(f.callsign, true)}<div><h2>${esc(label(f))}</h2>
        <div class="airline">${esc(f.airline || f.owner || '')}</div></div></div>
        ${routeLong(f) ? `<div class="route-big">${esc(routeLong(f))}${f.route_checked ? ' <span class="checked" title="Checked against its actual flight path">✓</span>' : ''}</div>` : ''}
        <div class="plane">${esc([f.aircraft || f.type, f.registration].filter(Boolean).join(' · '))}</div>
        <div class="stats">
          ${stat(`Seen ${['Today', 'Yesterday'].includes(dayLabel(f.first_seen)) ? dayLabel(f.first_seen).toLowerCase() : dayLabel(f.first_seen)}`, fmtTime(f.first_seen))}${stat('Closest', `${fmtDist(f.closest_miles)} ${compass(f.bearing)}`)}${stat('Altitude', fmtAlt(f.closest_alt_ft))}
        </div>
        <div class="note">${esc(why)}</div>
        ${f.offline ? '<div class="note">📴 Found afterwards: planespotter wasn’t running when it came over, so it was looked up in adsb.lol’s records.</div>' : ''}
        ${f.delay != null ? `<div class="note">⏱️ A regular: ${esc(Math.abs(f.delay) <= 10 ? 'on time' : `${Math.abs(f.delay)} min ${f.delay > 0 ? 'late' : 'early'}`)} compared with its usual time.</div>` : ''}
        ${f.noise_db != null ? `<div class="note">🎙️ ${esc(`+${f.noise_db} dB louder than the background`)}</div>` : ''}
        ${wholeNote()}
        ${actionsHtml(thing)}
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
      text = `${state.liveMeta && state.liveMeta.receiver ? 'Live from your receiver' : 'Live'} · ${n} plane${n === 1 ? '' : 's'} nearby${over ? ` · ${over} over your zone` : ''}`;
      const every = state.liveMeta ? state.liveMeta.interval : 0;
      if (every > 45) text += ` · updates every ${every >= 90 ? `${Math.round(every / 60)} min` : `${every} s`}`;
      if (state.quietNow) { text += ' · 🌙 quiet hours'; cls = 'quiet'; }
      if (meter.on) text += ' · 🎙️ listening';
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

  // ---------- look up! ----------
  /**
   * Planes over your zone now, or about to cross it if they fly straight on (within `horizon`
   * seconds), soonest first. For each: where to look (compass direction, and degrees above the
   * horizon: a fist at arm's length is about 10 degrees) and how soon.
   */
  function predictions(now, horizon = 300) {
    const { home, radius_miles: radius, home_elevation_ft: groundFt = 0, max_altitude_ft: ceiling } = state.config;
    const rad = Math.PI / 180;
    const out = [];
    for (const p of state.live) {
      const [lon, lat] = livePos(p, now);
      const miles = milesBetween(home.lat, home.lon, lat, lon);
      const az = bearingTo(home.lat, home.lon, lat, lon);
      let eta = null;
      if (miles <= radius && p.in_zone) eta = 0;
      else if (p.speed_kt && p.heading != null && !(ceiling && p.alt_ft > ceiling)) {
        const x = Math.sin(az * rad) * miles * MI, y = Math.cos(az * rad) * miles * MI;
        const v = p.speed_kt * KT, vx = Math.sin(p.heading * rad) * v, vy = Math.cos(p.heading * rad) * v;
        const tc = -(x * vx + y * vy) / (v * v);  // seconds until it's closest to home
        if (tc > 0 && tc < horizon && Math.hypot(x + vx * tc, y + vy * tc) / MI <= radius) eta = tc;
      }
      if (eta === null) continue;
      const height = Math.max(0, ((p.alt_ft || 0) - groundFt) * FT);
      const up = Math.atan2(height, Math.max(1, miles * MI)) / rad;
      out.push({ p, miles, az, up, eta });
    }
    return out.sort((x, y) => x.eta - y.eta || x.miles - y.miles);
  }

  const lookUpTarget = (now) => predictions(now, 300)[0] || null;

  function fists(degrees) {
    if (degrees >= 70) return 'almost straight up';
    const n = Math.round(degrees / 5) / 2;  // nearest half fist
    if (n < 0.5) return 'just above the horizon';
    const whole = Math.floor(n), half = n - whole ? '½' : '';
    return `about ${whole || ''}${half} fist${n > 1 ? 's' : ''} above the horizon`;
  }

  let lastLookUpHtml = '';
  function renderLookUp(now) {
    const el = $('#lookup');
    const sat = state.replay.on ? null : satTarget();
    if (sat) {
      // The space station (or another bright satellite) is crossing your sky right now.
      const html = `<div class="dial" aria-hidden="true"><span class="n">N</span>`
        + `<svg class="arrow" viewBox="0 0 24 24" style="transform: rotate(${Math.round(sat.az)}deg)"><path d="M12 2.5 18 20l-6-3.6L6 20z"/></svg></div>
        <div class="what"><div class="head">Look up · <strong>${compass(sat.az)}, ${Math.round(sat.el)}° up</strong></div>
        <div class="sub"><span>🛰️ <strong>${esc(sat.name)}</strong> is crossing your sky now: a bright, steady star moving fast</span></div></div>`;
      if (html !== lastLookUpHtml) { el.innerHTML = html; lastLookUpHtml = html; }
      el.hidden = false;
      el.onclick = null;
      return;
    }
    const target = state.replay.on ? null : lookUpTarget(now);
    if (!target) { el.hidden = true; lastLookUpHtml = ''; return; }
    const { p, miles, az, up, eta } = target;
    const info = { ...(infoFor(p) || {}), ...(checkedRoute(p.hex) || {}) };
    const when = eta === 0 ? 'overhead now' : eta < 60 ? `in ${Math.round(eta / 5) * 5 || 5} seconds` : `in ${Math.round(eta / 60)} min`;
    const note = skyNote(p, miles, az, up);
    const seen = sawIt(p.hex);
    const html = `<div class="dial" aria-hidden="true"><span class="n">N</span>`
      + `<svg class="arrow" viewBox="0 0 24 24" style="transform: rotate(${Math.round(az)}deg)"><path d="M12 2.5 18 20l-6-3.6L6 20z"/></svg></div>
      <div class="what"><div class="head">Look up · <strong>${compass(az)}, ${Math.round(up)}° up</strong></div>
      <div class="sub">${logo(p.callsign)}<span><strong>${esc(label(p))}</strong> ${esc(routeShort(info))} · ${when} · ${esc(fists(up))}</span></div>
      ${note ? `<div class="sky-note">${esc(note)}</div>` : ''}</div>
      <div class="lookup-buttons"><button class="saw${seen ? ' done' : ''}" data-saw="${esc(p.hex)}" data-callsign="${esc(p.callsign || '')}" title="Tap when you've spotted it in the sky">${seen ? '✓ Seen' : '👀 I saw it'}</button>
      ${p.type ? `<button class="saw guess" data-guess="${esc(p.hex)}" title="Guess what kind of plane it is">🎯 Guess</button>` : ''}</div>`;
    if (html !== lastLookUpHtml) { el.innerHTML = html; lastLookUpHtml = html; }
    el.hidden = false;
    el.onclick = (e) => { if (!e.target.closest('.saw')) select('live', p.hex); };
    // Speak once per plane, as it's about to come over (after its airline and route have loaded, if they're coming).
    if (state.voice && eta <= 45 && !state.quietNow && (infoFor(p) !== null || eta < 15)) {
      const last = state.spoken.get(p.hex) || 0;
      if (Date.now() - last > 20 * 60000) { state.spoken.set(p.hex, Date.now()); say(announcement(target, info, note)); }
    }
    // A chime when a plane is about to come over (once per plane).
    if (state.chime && eta <= 60) {
      const last = state.chimed.get(p.hex) || 0;
      if (Date.now() - last > 20 * 60000) { state.chimed.set(p.hex, Date.now()); chime(); }
    }
  }

  let audio = null;
  function chime() {
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      if (audio.state === 'suspended') audio.resume();
      const t0 = audio.currentTime;
      [880, 1318.5].forEach((freq, i) => {   // two soft notes, like an airport chime
        const osc = audio.createOscillator(), gain = audio.createGain();
        const start = t0 + i * 0.22;
        osc.type = 'sine';
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0, start);
        gain.gain.linearRampToValueAtTime(0.22, start + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.001, start + 1.1);
        osc.connect(gain).connect(audio.destination);
        osc.start(start);
        osc.stop(start + 1.2);
      });
    } catch { /* no sound available */ }
  }

  function setChime(on) {
    state.chime = on;
    saved.set('chime', on);
    const b = $('#btn-chime');
    b.setAttribute('aria-pressed', String(on));
    b.textContent = on ? '🔔 Chime on' : '🔕 Chime';
    if (on) chime();  // also unlocks sound in the browser, which needs a click first
  }

  // ---------- can you see it? clouds + daylight ----------
  const SOLID = ['BKN', 'OVC', 'OVX'];
  const COVER = { FEW: 'a few clouds', SCT: 'scattered clouds', BKN: 'broken cloud', OVC: 'overcast', OVX: 'fog' };

  async function loadSky() {
    try { state.sky = await getJSON('api/sky'); } catch { /* keep the last one */ }
    renderSkyLine();
  }

  /** Degrees between two directions in the sky (azimuth + altitude). */
  function angleBetween(az1, alt1, az2, alt2) {
    const r = Math.PI / 180;
    const c = Math.sin(alt1 * r) * Math.sin(alt2 * r) + Math.cos(alt1 * r) * Math.cos(alt2 * r) * Math.cos((az1 - az2) * r);
    return Math.acos(Math.max(-1, Math.min(1, c))) / r;
  }

  /** "Above the overcast at 1,800 ft: listen for it", "After dark: look for its lights"... or '' if it's easy to see. */
  function skyNote(p, miles, az, up) {
    const s = state.sky;
    if (!s) return '';
    const w = s.weather, sun = s.sun || {};
    if (w && p.alt_ft != null) {
      const height = p.alt_ft - (state.config.home_elevation_ft || 0);
      if (w.ceiling_ft != null && height > w.ceiling_ft + 200) {
        const layer = w.layers.find((l) => l.base_ft === w.ceiling_ft && SOLID.includes(l.cover)) || { cover: 'OVC' };
        const at = `${w.ceiling_ft.toLocaleString()} ft`;
        return layer.cover === 'BKN' ? `⛅ Above broken cloud at ${at}: you might catch it through a gap`
          : `☁️ Above the ${COVER[layer.cover]} at ${at}: listen for it`;
      }
      const slant = Math.hypot(miles, Math.max(0, height) / 5280);
      if (w.visibility_miles != null && w.visibility_miles < slant) return `🌫️ Visibility is only ${w.visibility_miles} mi, so it may be hard to see`;
    }
    if (sun.daylight === 'day' && sun.altitude > 0 && angleBetween(az, up, sun.azimuth, sun.altitude) < 20) {
      return '😎 It’s near the sun: don’t look straight at it';
    }
    if (sun.daylight === 'night') return '🌙 After dark: look for its flashing lights';
    if (sun.daylight === 'twilight') return '🌆 Getting dark: look for its lights';
    return '';
  }

  function renderSkyLine() {
    const el = $('#sky-line');
    const s = state.sky;
    if (!s) { el.hidden = true; return; }
    const w = s.weather, dark = s.sun && s.sun.daylight !== 'day';
    let text;
    if (!w) text = dark ? '🌙 Dark out' : '☀️ Daylight';
    else {
      const solid = w.layers.find((l) => SOLID.includes(l.cover));
      const top = w.layers[0];
      if (solid) text = `${solid.cover === 'BKN' ? '🌥️ Broken cloud' : solid.cover === 'OVX' ? '🌫️ Fog' : '☁️ Overcast'} at ${solid.base_ft.toLocaleString()} ft`;
      else if (top) text = `${top.cover === 'SCT' ? '⛅' : '🌤️'} ${COVER[top.cover][0].toUpperCase()}${COVER[top.cover].slice(1)} at ${top.base_ft.toLocaleString()} ft`;
      else text = dark ? '🌙 Clear night' : '☀️ Clear skies';
      if (w.visibility_miles != null && w.visibility_miles < 5) text += ` · 🌫️ ${w.visibility_miles} mi visibility`;
      if (dark && !text.startsWith('🌙')) text += ' · 🌙 dark';
    }
    el.textContent = text;
    el.title = w ? `${w.station} ${w.name} (${w.distance_miles} mi away), ${new Date(w.observed * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}:\n${w.raw}` : '';
    el.hidden = false;
  }

  // ---------- spoken announcements ----------
  const canSpeak = 'speechSynthesis' in window;

  function say(text) {
    if (!canSpeak) return;
    const u = new SpeechSynthesisUtterance(text);
    u.lang = navigator.language || 'en-US';
    u.rate = 1;
    window.speechSynthesis.speak(u);
  }

  const DIRECTION_WORDS = { N: 'north', NE: 'north-east', E: 'east', SE: 'south-east', S: 'south', SW: 'south-west', W: 'west', NW: 'north-west' };

  /** "Delta flight 573 from Fort Lauderdale, coming over in about 30 seconds. Look north-east, 25 degrees up." */
  function announcement({ p, az, up, eta }, info, note) {
    const number = /^[A-Z]{3}(\d+[A-Z]?)$/.exec(p.callsign || '');
    const airline = (info.airline || '').replace(/\s+(Air ?Lines|Airways|Airlines)$/i, '');
    const name = airline && number ? `${airline} flight ${number[1]}` : airline ? `${airline}, ${label(p)}` : label(p);
    const from = info.origin_city ? ` from ${info.origin_city}` : '';
    const when = eta === 0 ? ' is overhead now' : `, coming over in about ${eta < 60 ? `${Math.max(5, Math.round(eta / 5) * 5)} seconds` : 'a minute'}`;
    const where = up >= 70 ? 'Look straight up.' : `Look ${DIRECTION_WORDS[compass(az)]}, ${Math.round(up)} degrees up.`;
    const alt = p.alt_ft ? ` It's at ${(Math.round(p.alt_ft / 100) * 100).toLocaleString()} feet.` : '';
    const sky = note ? ` ${note.replace(/^\S+\s/, '').replace(/:\s*/, ', ')}.` : '';  // "After dark, look for its flashing lights."
    return `${name}${from}${when}. ${where}${alt}${sky}`;
  }

  function setVoice(on, announce = true) {
    state.voice = on && canSpeak;
    saved.set('voice', state.voice);
    const b = $('#btn-voice');
    b.setAttribute('aria-pressed', String(state.voice));
    b.textContent = state.voice ? '🗣️ Voice on' : '🔇 Voice';
    if (!canSpeak) { b.disabled = true; b.title = 'This browser can’t speak'; }
    if (announce && state.voice) say('Voice announcements are on.');  // also unlocks speech, which needs a tap first
  }

  // ---------- coming up next ----------
  const countdown = (secs) => (secs < 1 ? 'now' : `${Math.floor(secs / 60)}:${String(Math.floor(secs % 60)).padStart(2, '0')}`);

  let lastUpNext = '';
  function renderUpNext(now) {
    const el = $('#upnext');
    const next = state.tab === 'live' && state.config && !state.replay.on ? predictions(now, 600).slice(0, 4) : [];
    if (!next.length) { el.hidden = true; lastUpNext = ''; return; }
    const html = '<div class="upnext-head">Coming over next <span>if they fly straight on</span></div>' + next.map(({ p, az, eta }) => {
      const info = { ...(infoFor(p) || {}), ...(checkedRoute(p.hex) || {}) };
      return `<button class="up" data-hex="${esc(p.hex)}">${logo(p.callsign)}
        <span class="who"><strong>${esc(label(p))}</strong> <span class="r">${esc(routeShort(info))}</span></span>
        <span class="from">${eta === 0 ? 'overhead' : compass(az)}</span>
        <span class="t${eta === 0 ? ' now' : ''}">${eta === 0 ? 'now' : countdown(eta)}</span></button>`;
    }).join('');
    if (html !== lastUpNext) { el.innerHTML = html; lastUpNext = html; }
    el.hidden = false;
  }

  // ---------- yours: "I saw it" and your photos ----------
  async function postJSON(url, body) {
    const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  async function loadYours() {
    try { state.yours = await getJSON('api/yours'); } catch { /* keep what we had */ }
    lastDetailHtml = '';
    renderDetail();
  }

  /** Did you tap "I saw it" on this plane (around time `at`, default now)? */
  const sawIt = (hex, at = Date.now() / 1000) => state.yours.eyes.some(([h, t]) => h === hex && Math.abs(t - at) < 3600);

  async function toggleSaw(hex, callsign, at) {
    const undo = sawIt(hex, at);
    try {
      state.yours = await postJSON('api/saw', { hex, callsign, at, undo });
    } catch (err) { toast(`Couldn’t save that: ${err.message}`); return; }
    lastLookUpHtml = ''; lastDetailHtml = '';
    renderDetail();
    renderLookUp(performance.now());
    const s = state.yours.streak;
    if (!undo && s) toast(s.current > 1 ? `👀 Nice spot! ${s.current}-day streak 🔥` : '👀 Nice spot! Spot one tomorrow to start a streak.');
    if (state.tab === 'collection') loadCollection();
  }

  /** Your photos of this flight (a spotted one), or of this plane in the last few hours (a live one). */
  function photosOf(thing) {
    const all = state.yours.photos;
    if (thing.flight) return all.filter((ph) => ph.pass_id === thing.flight.id);
    const since = Date.now() / 1000 - 6 * 3600;
    return all.filter((ph) => ph.hex === thing.plane.hex && ph.added_at > since);
  }

  let toastTimer = 0;
  function toast(text) {
    let el = $('#toast');
    if (!el) { el = document.createElement('div'); el.id = 'toast'; el.className = 'toast'; document.body.appendChild(el); }
    el.textContent = text;
    el.classList.add('on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('on'), 3500);
  }

  /** Shrink a picture in the browser (phones take huge ones) to a JPEG no bigger than `max` pixels. */
  async function shrink(file, max, quality) {
    const img = await new Promise((resolve, reject) => {
      const im = new Image();
      im.onload = () => resolve(im);
      im.onerror = () => reject(new Error('This browser can’t open that kind of picture. Try a JPEG.'));
      im.src = URL.createObjectURL(file);
    });
    const k = Math.min(1, max / Math.max(img.naturalWidth, img.naturalHeight));
    const c = document.createElement('canvas');
    c.width = Math.round(img.naturalWidth * k);
    c.height = Math.round(img.naturalHeight * k);
    c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
    URL.revokeObjectURL(img.src);
    return { data: c.toDataURL('image/jpeg', quality), width: c.width, height: c.height };
  }

  let photoTarget = null;
  function pickPhoto() {
    const thing = selectedThing();
    if (!thing) return;
    photoTarget = thing.flight ? { pass_id: thing.flight.id, hex: thing.flight.hex, callsign: thing.flight.callsign }
      : { hex: thing.plane.hex, callsign: thing.plane.callsign };
    $('#photo-input').value = '';
    $('#photo-input').click();
  }

  async function uploadPhoto(file) {
    if (!file || !photoTarget) return;
    toast('Adding your photo…');
    try {
      const full = await shrink(file, 1600, 0.85);
      const thumb = await shrink(file, 480, 0.8);
      state.yours = await postJSON('api/photo', { ...photoTarget, full: full.data, thumb: thumb.data, width: full.width, height: full.height });
      toast('📸 Photo added');
    } catch (err) {
      toast(err.message);
    }
    lastDetailHtml = '';
    renderDetail();
    if (state.tab === 'collection') loadCollection();
  }

  // ---------- the overlay (your photos, share cards) ----------
  function openOverlay(html, wide = false) {
    $('#overlay-body').innerHTML = html;
    $('#overlay .sheet').classList.toggle('wide', wide);
    $('#overlay').hidden = false;
  }
  function closeOverlay() {
    $('#overlay').hidden = true;
    for (const img of document.querySelectorAll('#overlay-body img[data-blob]')) URL.revokeObjectURL(img.src);
    $('#overlay-body').innerHTML = '';
  }

  function viewPhoto(id) {
    const ph = state.yours.photos.find((x) => x.id === Number(id));
    if (!ph) return;
    const when = new Date(ph.added_at * 1000).toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
    const route = ph.origin && ph.destination ? ` · ${ph.origin} → ${ph.destination}` : '';
    openOverlay(`<img class="big-photo" src="photo/${ph.id}.jpg" alt="">
      <div class="overlay-row"><div><strong>${esc(ph.callsign || ph.registration || ph.hex.toUpperCase())}</strong>${esc(route)}
      <div class="muted">${esc([ph.airline, ph.type].filter(Boolean).join(' · '))}${ph.airline || ph.type ? ' · ' : ''}added ${esc(when)}</div></div>
      <div class="overlay-actions">${ph.pass_id ? `<button class="linkbtn" data-open-flight="${ph.pass_id}">Show the flight</button>` : ''}
      <a class="linkbtn" href="photo/${ph.id}.jpg" download="planespotter-${esc(ph.callsign || ph.hex)}-${ph.id}.jpg">Download</a>
      <button class="linkbtn danger" data-delete-photo="${ph.id}">Delete</button></div></div>`, true);
  }

  async function deletePhoto(id) {
    if (!window.confirm('Delete this photo? This can’t be undone.')) return;
    try { state.yours = await postJSON('api/photo/delete', { photo: Number(id) }); } catch (err) { toast(err.message); return; }
    closeOverlay();
    lastDetailHtml = '';
    renderDetail();
    if (state.tab === 'collection') loadCollection();
  }

  /** Show a spotted flight from anywhere (the collection, a photo...), loading older history if needed. */
  function openFlight(id) {
    closeOverlay();
    state.pendingSelect = { kind: 'history', key: String(id) };
    if (!state.flights.some((f) => String(f.id) === String(id)) && state.days !== 0) {
      state.days = 0;  // it's older than the list shows: show everything
      saved.set('days', 0);
      for (const c of document.querySelectorAll('[data-days]')) c.setAttribute('aria-pressed', String(c.dataset.days === '0'));
    }
    setTab('history');
  }

  // ---------- share cards ----------
  const CARD_FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';

  function loadImage(src) {
    return new Promise((resolve) => {
      const im = new Image();
      im.onload = () => resolve(im);
      im.onerror = () => resolve(null);
      im.src = src;
    });
  }

  /** Write text no wider than `max`, shrinking the font if it has to. */
  function fitText(g, text, x, y, size, weight, max, color) {
    let s = size;
    do { g.font = `${weight} ${s}px ${CARD_FONT}`; s -= 2; } while (g.measureText(text).width > max && s > 18);
    g.fillStyle = color;
    g.fillText(text, x, y);
  }

  function roundRect(g, x, y, w, h, r) {
    g.beginPath();
    g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r);
    g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath();
  }

  /** The flight's path drawn on its own (no map, and nothing that shows where you live). */
  function drawPath(g, path, box, color) {
    if (path.length < 2) return false;
    let pts = path.map((q) => [q[1], q[0]]);  // [lon, lat]
    const lons = pts.map((q) => q[0]);
    if (Math.max(...lons) - Math.min(...lons) > 180) pts = pts.map(([lo, la]) => [lo < 0 ? lo + 360 : lo, la]);
    const merc = ([lo, la]) => [lo * Math.PI / 180, Math.log(Math.tan(Math.PI / 4 + (Math.max(-85, Math.min(85, la)) * Math.PI) / 360))];
    const xy = pts.map(merc);
    const xs = xy.map((q) => q[0]), ys = xy.map((q) => q[1]);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const k = Math.min(box.w / Math.max(maxX - minX, 1e-4), box.h / Math.max(maxY - minY, 1e-4));
    const ox = box.x + (box.w - (maxX - minX) * k) / 2, oy = box.y + (box.h - (maxY - minY) * k) / 2;
    const screen = xy.map(([x, y]) => [ox + (x - minX) * k, oy + (maxY - y) * k]);
    g.save();
    g.strokeStyle = 'rgba(255,255,255,0.06)';
    g.lineWidth = 2;
    for (let i = 0; i <= 8; i++) {
      const gx = box.x + (box.w * i) / 8, gy = box.y + (box.h * i) / 8;
      g.beginPath(); g.moveTo(gx, box.y); g.lineTo(gx, box.y + box.h); g.stroke();
      g.beginPath(); g.moveTo(box.x, gy); g.lineTo(box.x + box.w, gy); g.stroke();
    }
    g.lineJoin = 'round'; g.lineCap = 'round';
    g.shadowColor = color; g.shadowBlur = 28;
    g.strokeStyle = color; g.lineWidth = 9;
    g.beginPath();
    screen.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
    g.stroke();
    g.shadowBlur = 0;
    for (const [x, y] of [screen[0], screen[screen.length - 1]]) {
      g.fillStyle = '#0b0f14'; g.beginPath(); g.arc(x, y, 14, 0, 7); g.fill();
      g.fillStyle = color; g.beginPath(); g.arc(x, y, 9, 0, 7); g.fill();
    }
    g.restore();
    return screen;
  }

  async function makeShareCard(thing) {
    const W = 1080, H = 1350, ACC = '#ffb020', MUTED = '#9aa7b6';
    const c = document.createElement('canvas');
    c.width = W; c.height = H;
    const g = c.getContext('2d');
    const bg = g.createLinearGradient(0, 0, 0, H);
    bg.addColorStop(0, '#101826'); bg.addColorStop(1, '#070a0e');
    g.fillStyle = bg; g.fillRect(0, 0, W, H);

    const f = thing.flight, p = thing.plane;
    const info = p ? { ...(infoFor(p) || {}), ...(checkedRoute(p.hex) || {}) } : f;
    const callsign = label(f || p);
    const mine = photosOf(thing)[0];
    const photo = mine ? await loadImage(`photo/${mine.id}.jpg`) : null;
    if (photo) {
      const k = Math.max(W / photo.naturalWidth, 720 / photo.naturalHeight);
      const w = photo.naturalWidth * k, h = photo.naturalHeight * k;
      g.drawImage(photo, (W - w) / 2, (720 - h) / 2, w, h);
      const fade = g.createLinearGradient(0, 480, 0, 730);
      fade.addColorStop(0, 'rgba(16,24,38,0)'); fade.addColorStop(1, 'rgba(16,24,38,1)');
      g.fillStyle = fade; g.fillRect(0, 480, W, 260);
    } else {
      const w = state.whole && state.whole.key === selKeyOf(state.selected) && state.whole.path.length > 1 ? state.whole.path : null;
      let path = w || (f ? f.path : []);
      if (p && path.length) path = [...path, [p.lat, p.lon, p.alt_ft || 0, 0]];
      const ends = drawPath(g, path, { x: 90, y: 110, w: 900, h: 520 }, ACC);
      if (ends) {
        g.font = `700 34px ${CARD_FONT}`;
        g.fillStyle = '#e8edf3';
        const tag = (code, [x, y]) => { if (code) { const tw = g.measureText(code).width; g.fillText(code, Math.min(W - 60 - tw, Math.max(60, x - tw / 2)), y < 160 ? y + 56 : y - 26); } };
        tag(info.origin, ends[0]);
        tag(p ? '' : info.destination, ends[ends.length - 1]);
      } else {
        g.font = `200px ${CARD_FONT}`; g.fillStyle = ACC; g.fillText('✈', W / 2 - 100, 440);
      }
    }

    // Who: logo, callsign, airline.
    const code = airlineCode((f || p).callsign);
    const logoImg = code && state.logoOK.has(code) ? await loadImage(`logo/${code}.png`) : null;
    let x = 80;
    if (logoImg) {
      g.fillStyle = '#fff'; roundRect(g, 80, 760, 120, 120, 26); g.fill();
      const k = Math.min(96 / logoImg.naturalWidth, 96 / logoImg.naturalHeight);
      g.drawImage(logoImg, 140 - (logoImg.naturalWidth * k) / 2, 820 - (logoImg.naturalHeight * k) / 2, logoImg.naturalWidth * k, logoImg.naturalHeight * k);
      x = 230;
    }
    fitText(g, callsign, x, 830, 78, 800, W - 80 - x, '#ffffff');
    fitText(g, info.airline || info.owner || (f || p).registration || '', x, 880, 38, 500, W - 80 - x, MUTED);

    // Where from and to.
    if (hasRoute(info)) {
      fitText(g, `${info.origin || '?'} → ${info.destination || '?'}`, 80, 1020, 120, 800, W - 160, ACC);
      fitText(g, [info.origin_city, info.destination_city].filter(Boolean).join(' to '), 80, 1076, 40, 500, W - 160, '#e8edf3');
    }
    const plane = [info.aircraft_desc || (f && f.aircraft) || (f || p).type, (f || p).registration].filter(Boolean).join(' · ');
    fitText(g, plane, 80, 1130, 36, 500, W - 160, MUTED);

    // The numbers.
    const when = new Date((f ? f.first_seen : Date.now() / 1000) * 1000);
    const stats = [
      ['Spotted', `${when.toLocaleDateString([], { day: 'numeric', month: 'short' })}, ${when.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`],
      ['Altitude', fmtAlt(f ? f.closest_alt_ft : p.alt_ft)],
      [f ? 'Closest' : 'Distance', fmtDist(f ? f.closest_miles : p.distance_miles)],
    ];
    stats.forEach(([k, v], i) => {
      const sx = 80 + i * 310;
      g.font = `700 24px ${CARD_FONT}`; g.fillStyle = MUTED; g.fillText(k.toUpperCase(), sx, 1210);
      fitText(g, v, sx, 1256, 40, 700, 290, '#ffffff');
    });
    g.strokeStyle = 'rgba(255,255,255,0.12)'; g.lineWidth = 2;
    g.beginPath(); g.moveTo(80, 1160); g.lineTo(W - 80, 1160); g.stroke();
    g.font = `800 28px ${CARD_FONT}`; g.fillStyle = MUTED;
    const brand = 'plane';
    const bw = g.measureText('planespotter').width;
    g.fillText(brand, W - 80 - bw, 1312);
    g.fillStyle = ACC; g.fillText('spotter', W - 80 - bw + g.measureText(brand).width, 1312);
    return new Promise((resolve) => c.toBlob(resolve, 'image/png'));
  }

  async function shareSelected() {
    const thing = selectedThing();
    if (!thing) return;
    toast('Making your card…');
    const blob = await makeShareCard(thing);
    if (!blob) { toast('Couldn’t make the picture.'); return; }
    const name = `${label(thing.flight || thing.plane)}.png`;
    const file = new File([blob], name, { type: 'image/png' });
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      try { await navigator.share({ files: [file], title: name }); toast('Shared ✈'); return; }
      catch (err) { if (err.name === 'AbortError') return; }
    }
    const url = URL.createObjectURL(blob);
    openOverlay(`<img class="share-card" data-blob="1" src="${url}" alt="Share card for ${esc(name)}">
      <div class="overlay-row"><div class="muted">${'ontouchstart' in window ? 'Press and hold the picture to save it or send it.' : 'Save the picture, then send it however you like.'}</div>
      <div class="overlay-actions"><a class="btn" href="${url}" download="${esc(name)}">Download</a></div></div>`);
  }

  // ---------- the noise tab ----------
  async function loadNoise() {
    try { state.noise = await getJSON(`api/noise?days=${state.noiseDays}`); } catch { /* keep what we had */ }
    if (state.tab === 'noise') renderList();
  }

  const shortWhen = (ts) => new Date(ts * 1000).toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

  function noiseRow(f, right) {
    return `<button class="crow flight" data-open-flight="${f.id}">${logo(f.callsign)}<span class="cname"><strong>${esc(f.label)}</strong>
      <span class="muted">${esc([f.aircraft, shortWhen(f.when)].filter(Boolean).join(' · '))}</span></span><span class="cnum">${esc(right)}</span></button>`;
  }

  function noiseHtml() {
    const n = state.noise;
    const secure = window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia;
    const meterCard = `<div class="meter-card">
      <div class="meter-head"><strong>🎙️ Noise meter</strong>
        <button class="chip" data-action="meter" aria-pressed="${meter.on}" ${secure ? '' : 'disabled'}>${meter.on ? 'Stop listening' : 'Start listening'}</button></div>
      <div class="meter"><div class="meter-fill" id="meter-fill"></div></div>
      <div class="meter-note" id="meter-note">${secure
        ? (meter.on ? 'Listening…' : 'Uses this device’s microphone to measure how loud each plane over your zone is. Leave this page open (it can be in the background). Nothing is recorded; only the loudness level, once a second, is kept for a few days.')
        : `The microphone only works on the computer running planespotter (<a href="http://localhost:${location.port || 8765}/">localhost</a>)${state.config && state.config.secure_url ? ` or on the <a href="${esc(state.config.secure_url)}/">secure address</a>` : ''}.`}</div>
    </div>`;
    if (!n) return `<div class="collection">${meterCard}${empty('Loading the noise log…')}</div>`;
    const tile = (k, v, note) => `<div class="ctile"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div>${note ? `<div class="note">${esc(note)}</div>` : ''}</div>`;
    const range = [7, 30, 90].map((d) => `<button class="chip" data-noise-days="${d}" aria-pressed="${d === n.days}">${d} days</button>`).join('');
    const nights = n.nights.map((e) => e.count);
    const tips = n.nights.map((e) => `Night of ${new Date(`${e.date}T12:00:00`).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })} · ${e.count} plane${e.count === 1 ? '' : 's'}`);
    const step = Math.max(1, Math.round(n.nights.length / 4));
    const ticks = n.nights.map((e, i) => [i, e.date]).filter(([i]) => i % step === 0)
      .map(([i, d]) => [i, new Date(`${d}T12:00:00`).toLocaleDateString([], { day: 'numeric', month: 'short' })]);
    const loudest = n.loudest.length ? n.loudest[0] : null;
    return `<div class="collection">
      ${meterCard}
      <div class="filters noise-range">${range}</div>
      <div class="ctiles">${tile('Planes over your zone', n.total.toLocaleString())}
        ${tile('Low', n.low_count.toLocaleString(), `under ${n.low_ft.toLocaleString()} ft above you`)}
        ${tile('At night', n.night_count.toLocaleString(), `${n.night[0]}–${n.night[1]}`)}
        ${tile('Loudest measured', loudest ? `+${loudest.louder_db} dB` : '—', loudest ? loudest.label : 'turn on the noise meter')}</div>
      <h3>Planes at night <span>${n.night[0]}–${n.night[1]}</span></h3>
      ${columnChart(nights, tips, ticks)}
      ${n.loudest.length ? `<h3>Loudest planes <span>louder than the background</span></h3>${n.loudest.map((f) => noiseRow(f, `+${f.louder_db} dB`)).join('')}` : ''}
      <h3>Lowest <span>height above you</span></h3>
      ${n.lowest.length ? n.lowest.map((f) => noiseRow(f, fmtAlt(f.above_home_ft))).join('') : '<p class="csub">No low planes in this time.</p>'}
      <h3>Latest at night</h3>
      ${n.latest_night.length ? n.latest_night.map((f) => noiseRow(f, fmtAlt(f.alt_ft))).join('') : '<p class="csub">No planes at night in this time.</p>'}
      <h3>Spreadsheet</h3>
      <p class="csub">Every plane over your zone with its time, height, aircraft and (if measured) loudness. Opens in Numbers, Excel or Google Sheets: handy for a complaint to the airport or your council.</p>
      <div class="downloads"><a class="btn" href="api/noise.csv?days=${n.days}" download>⬇ Last ${n.days} days</a>
        <a class="btn ghost" href="api/noise.csv?days=0" download>⬇ Everything</a></div>
    </div>`;
  }

  // ---------- noise meter (the microphone) ----------
  // One level per second (the loudest moment of that second, in dB below full scale) goes to
  // planespotter, which matches it to the planes going over. The sound itself never leaves the browser.
  const meter = { on: false, ctx: null, stream: null, node: null, sec: 0, max: -Infinity, level: null, recent: [], queue: [], sending: false };

  async function startMeter() {
    try {
      meter.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },  // the real level, not "improved"
      });
    } catch (err) {
      toast(err.name === 'NotAllowedError' ? 'Microphone permission was turned down. Allow it in the browser’s address bar and try again.' : `No microphone: ${err.message}`);
      return;
    }
    meter.ctx = new (window.AudioContext || window.webkitAudioContext)();
    const src = meter.ctx.createMediaStreamSource(meter.stream);
    // A ScriptProcessor keeps running when the tab is in the background (timers there get slowed down).
    meter.node = meter.ctx.createScriptProcessor(4096, 1, 1);
    meter.node.onaudioprocess = (e) => {
      const d = e.inputBuffer.getChannelData(0);
      let sum = 0;
      for (let i = 0; i < d.length; i++) sum += d[i] * d[i];
      const db = 10 * Math.log10(sum / d.length + 1e-12);
      const sec = Math.floor(Date.now() / 1000);
      if (sec !== meter.sec) {
        if (meter.sec && meter.max > -Infinity) gotSecond(meter.sec, meter.max);
        meter.sec = sec; meter.max = db;
      } else meter.max = Math.max(meter.max, db);
      meter.level = db;
    };
    src.connect(meter.node);
    meter.node.connect(meter.ctx.destination);  // silent, but needed for it to run
    meter.on = true;
    if (state.tab === 'noise') renderList();
  }

  function stopMeter() {
    meter.on = false;
    if (meter.node) meter.node.disconnect();
    if (meter.stream) meter.stream.getTracks().forEach((t) => t.stop());
    if (meter.ctx) meter.ctx.close();
    Object.assign(meter, { ctx: null, stream: null, node: null, level: null, recent: [] });
    sendLevels();
    if (state.tab === 'noise') renderList();
  }

  function gotSecond(sec, db) {
    meter.queue.push([sec, Math.round(db * 10) / 10]);
    meter.recent.push(db);
    if (meter.recent.length > 120) meter.recent.shift();
    if (meter.queue.length >= 10) sendLevels();
  }

  async function sendLevels() {
    if (meter.sending || !meter.queue.length) return;
    meter.sending = true;
    const batch = meter.queue.splice(0, 900);
    try {
      const r = await postJSON('api/noise/levels', { samples: batch });
      if (r.measured) loadNoise();
    } catch {
      meter.queue.unshift(...batch.slice(-600));  // try again with the next batch (planespotter may be restarting)
    }
    meter.sending = false;
  }

  /** The live bar: how much louder than the background (the quieter 20% of the last two minutes) it is now. */
  function renderMeter() {
    const fill = $('#meter-fill'), note = $('#meter-note');
    if (!fill || !meter.on || meter.level == null) return;
    const sorted = meter.recent.slice().sort((x, y) => x - y);
    const bg = sorted.length ? sorted[Math.floor(sorted.length * 0.2)] : meter.level;
    const over = Math.max(0, meter.level - bg);
    fill.style.width = `${Math.min(100, (over / 30) * 100)}%`;
    fill.classList.toggle('loud', over > 12);
    const overhead = state.live.filter((p) => p.in_zone).map(label);
    note.textContent = `Now: ${over < 1 ? 'background level' : `+${over.toFixed(0)} dB over the background`}`
      + (overhead.length ? ` · overhead: ${overhead.join(', ')}` : '') + ' · keep this page open';
  }

  // ---------- collection extras: plane of the day, streak, records, your photos ----------
  function extrasHtml(c) {
    const star = c.star;
    const s = c.streak || { current: 0, best: 0, total: 0 };
    const starHtml = star ? `<button class="star" data-open-flight="${star.id}">${logo(star.callsign, true)}
        <div><div class="k">⭐ Plane of the ${star.yesterday ? 'day (yesterday)' : 'day'}</div>
        <div class="v">${esc(star.label)} <span class="muted">${esc([star.airline, star.route].filter(Boolean).join(' · '))}</span></div>
        <div class="why">${esc(star.why)}</div></div></button>` : '';
    const streak = `<div class="ctiles">
      <div class="ctile"><div class="k">🔥 Streak</div><div class="v">${s.current} day${s.current === 1 ? '' : 's'}</div>
        <div class="note">${s.current && !s.alive_today ? 'spot one today to keep it going' : `best ${s.best} day${s.best === 1 ? '' : 's'}`}</div></div>
      <div class="ctile"><div class="k">👀 Seen with your own eyes</div><div class="v">${s.total.toLocaleString()}</div>
        <div class="note">tap “I saw it” when you spot one</div></div></div>`;
    const recs = (c.records || []).map((r) => `<button class="crow flight" data-open-flight="${r.id}"><span class="cflag">${r.emoji}</span>
        <span class="cname"><strong>${esc(r.name)}</strong> <span class="muted">${esc(r.label)}${r.route ? ` · ${esc(r.route)}` : ''} · ${esc(new Date(r.when * 1000).toLocaleDateString([], { day: 'numeric', month: 'short' }))}</span></span>
        <span class="cnum">${esc(r.value)}</span></button>`).join('');
    const b = c.busiest || {};
    const busy = [b.day ? `<div class="crow"><span class="cflag">📅</span><span class="cname"><strong>Busiest day</strong> <span class="muted">${esc(new Date(`${b.day.date}T12:00:00`).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' }))}</span></span><span class="cnum">${b.day.count} planes</span></div>` : '',
      b.hour ? `<div class="crow"><span class="cflag">⏱️</span><span class="cname"><strong>Busiest hour</strong> <span class="muted">${esc(shortWhen(b.hour.start))}</span></span><span class="cnum">${b.hour.count} planes</span></div>` : ''].join('');
    const photos = state.yours.photos;
    const gallery = photos.length
      ? `<div class="gallery">${photos.slice(0, 60).map((ph) => `<button data-view-photo="${ph.id}" title="${esc(ph.callsign || ph.hex)}"><img src="photo/${ph.id}-thumb.jpg" alt="${esc(ph.callsign || '')}" loading="lazy"></button>`).join('')}</div>`
      : '<p class="csub">Select a plane and tap “📷 Add photo” to keep your own pictures of it here.</p>';
    return {
      top: starHtml + streak + `<div class="wrapped-links">
          <button class="btn ghost" data-wrapped="${esc(c.year)}">✨ Your ${esc(c.year)} in planes</button>
          <button class="btn ghost" data-wrapped="${esc(c.month)}">${esc(new Date(`${c.month}-15T12:00:00`).toLocaleDateString([], { month: 'long' }))} in planes</button></div>`
        + bingoHtml(c.bingo)
        + (c.quiz && c.quiz.answered ? `<div class="ctiles"><div class="ctile"><div class="k">🎯 Guess the plane</div><div class="v">${c.quiz.right} / ${c.quiz.answered}</div>
            <div class="note">${c.quiz.percent}% right · best run ${c.quiz.best_run}</div></div></div>` : ''),
      records: recs || busy ? `<h3>Records</h3>${recs}${busy}` : '',
      photos: `<h3>Your photos <span>${photos.length}</span></h3>${gallery}`
        + ((c.friends || []).length ? `<h3>Old friends <span>planes you see again and again</span></h3>` + c.friends.map((f) => `
          <button class="crow flight" data-profile="${esc(f.hex)}">${logo(f.callsign)}<span class="cname"><strong>${esc(f.registration || f.callsign)}</strong>
          <span class="muted">${esc([f.aircraft, f.airline].filter(Boolean).join(' · '))}</span></span><span class="cnum">${f.times}×</span></button>`).join('') : ''),
    };
  }

  // ---------- runway flow, today's regulars, space station ----------
  async function loadFlow() {
    try { state.flow = await getJSON('api/flow'); } catch { /* keep the last one */ }
    const el = $('#flow-line');
    const text = state.flow && state.flow.text;
    el.hidden = !text;
    if (text) { el.textContent = `🛬 ${text}`; el.title = 'Worked out from planes landing and taking off near the airport'; }
  }

  async function loadToday() {
    try { state.today = await getJSON('api/today'); } catch { /* keep the last one */ }
    if (state.tab === 'live') renderListSoon();
  }

  async function loadSpace() {
    try {
      const data = await getJSON('api/space');
      const t = Date.now() / 1000;
      for (const tr of data.tracks) tr.loadedAt = t;
      state.space = data;
    } catch { /* keep the last one */ }
    if (state.tab === 'live') renderListSoon();
  }

  const clockTime = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  function dayWord(ts) {
    const d = dayLabel(ts);
    const h = new Date(ts * 1000).getHours();
    if (d === 'Today') return h >= 17 ? 'Tonight' : 'Today';
    if (d === 'Yesterday') return d;
    const tomorrow = new Date(); tomorrow.setDate(tomorrow.getDate() + 1);
    return sameDay(new Date(ts * 1000), tomorrow) ? (h >= 17 ? 'Tomorrow night' : 'Tomorrow') : d;
  }

  /** "Due today" and "space station" sections at the bottom of In the air. */
  function extraLiveSections() {
    let html = '';
    const b = state.today;
    if (b && b.flights && b.flights.length) {
      const icon = { seen: '✓', due: '🕒', late: '⏳', missing: '—' };
      html += `<div class="list-section">Your regular flights today</div>` + b.flights.map((f) => `
        <div class="reg ${f.state}"${f.id ? ` data-open-flight="${f.id}"` : ''}><span class="t">${esc(f.usual)}</span>
          ${logo(f.callsign)}<span class="who"><strong>${esc(f.callsign)}</strong> <span class="muted">${esc(f.origin && f.destination ? `${f.origin} → ${f.destination}` : f.airline || '')}</span></span>
          <span class="st">${icon[f.state] || ''} ${esc(f.text)}</span></div>`).join('');
    }
    const passes = ((state.space && state.space.passes) || []).filter((p) => p.visible && p.max_el >= 15).slice(0, 3);
    if (passes.length) {
      html += '<div class="list-section">🛰️ Space station and friends: when to look</div>' + passes.map((p) => `
        <div class="reg sat"><span class="t">${esc(clockTime(p.visible_from || p.start))}</span>
          <span class="who"><strong>${esc(p.name)}</strong> <span class="muted">${esc(dayWord(p.visible_from || p.start))}</span></span>
          <span class="st">${esc(p.start_dir)} → ${esc(p.end_dir)}, up to ${p.max_el}°</span></div>`).join('')
        + '<p class="hint">A bright, steady star moving fast across the sky, with no blinking lights.</p>';
    }
    return html;
  }

  /** A satellite crossing your sky right now (visible), from its track: where to look. */
  function satTarget() {
    const now = Date.now() / 1000;
    for (const tr of (state.space && state.space.tracks) || []) {
      if (!tr.visible) continue;
      const pts = tr.points;
      for (let i = 1; i < pts.length; i++) {
        if (pts[i][0] < now) continue;
        const [t0, az0, el0] = pts[i - 1], [t1, az1, el1] = pts[i];
        const k = (now - t0) / Math.max(1, t1 - t0);
        if (k < 0 || k > 1) break;
        let daz = az1 - az0; if (daz > 180) daz -= 360; if (daz < -180) daz += 360;
        const az = (az0 + daz * k + 360) % 360, el = el0 + (el1 - el0) * k;
        return el >= 8 ? { name: tr.name, az, el, number: tr.number } : null;
      }
    }
    return null;
  }

  // ---------- follow a flight ----------
  const isFollowing = (hex) => (state.yours.following || []).some((f) => f.hex === hex);

  async function toggleFollow(hex, callsign) {
    const undo = isFollowing(hex);
    try { state.yours = await postJSON('api/follow', { hex, callsign, undo }); }
    catch (err) { toast(`Couldn’t do that: ${err.message}`); return; }
    lastDetailHtml = '';
    renderDetail();
    toast(undo ? 'Stopped following' : '🔔 Following: you’ll get a notification on your phone when it lands');
  }

  // ---------- guess the plane ----------
  async function startQuiz(hex) {
    let q;
    try { q = await getJSON(`api/quiz?hex=${encodeURIComponent(hex)}`); }
    catch (err) { toast(err.message); return; }
    state.quiz = { ...q, hex };
    const p = state.live.find((x) => x.hex === hex);
    const where = p ? `${compass(p.bearing)} of you, ${fmtAlt(p.alt_ft)}` : '';
    openOverlay(`<div class="quiz"><div class="k">🎯 Guess the plane</div>
      <h2>What kind of plane is ${esc(p ? label(p) : 'it')}?</h2>
      <p class="muted">Look up (${esc(where)}) and pick one. Score so far: ${q.score.right} of ${q.score.answered}.</p>
      <div class="quiz-options">${q.options.map((o) => `<button class="btn ghost" data-quiz-answer="${esc(o)}">${esc(o)}</button>`).join('')}</div>
      <div id="quiz-result"></div></div>`);
  }

  async function answerQuiz(chosen) {
    const q = state.quiz;
    if (!q) return;
    let r;
    try { r = await postJSON('api/quiz', { id: q.id, chosen }); } catch (err) { toast(err.message); return; }
    for (const b of document.querySelectorAll('[data-quiz-answer]')) {
      b.disabled = true;
      b.classList.toggle('right', b.dataset.quizAnswer === r.answer);
      b.classList.toggle('wrong', b.dataset.quizAnswer === chosen && !r.correct);
    }
    const p = state.live.find((x) => x.hex === q.hex);
    const info = p ? infoFor(p) || {} : {};
    $('#quiz-result').innerHTML = `<p class="quiz-verdict">${r.correct ? '✓ Right!' : '✗ Not quite.'} It's ${esc(info.aircraft_desc || r.answer)}${info.airline ? ` (${esc(info.airline)})` : ''}.</p>
      <p class="muted">Score: ${r.score.right} of ${r.score.answered}${r.score.run > 1 ? ` · ${r.score.run} right in a row` : ''}</p>`;
    state.quiz = null;
  }

  // ---------- spotting bingo, your year, plane profiles ----------
  function bingoHtml(b) {
    if (!b) return '';
    const lit = new Set(b.line_cells.flat());
    return `<h3>Spotting bingo <span>${b.filled} of 9${b.lines ? ` · ${b.lines} line${b.lines === 1 ? '' : 's'}` : ''}</span></h3>
      <div class="bingo">${b.squares.map((sq, i) => `<div class="sq${sq.by ? ' hit' : ''}${lit.has(i) ? ' line' : ''}"
        ${sq.by ? `data-open-flight="${sq.by.id}" title="${esc(sq.by.label)}, ${esc(shortWhen(sq.by.when))}"` : ''}>
        <div class="e">${sq.emoji}</div><div class="x">${esc(sq.text)}</div>${sq.by ? `<div class="by">${esc(sq.by.label)}</div>` : ''}</div>`).join('')}</div>
      <p class="hint">A new card every Monday. Squares fill in by themselves as planes come over.${b.full_house ? ' 🎉 Full house!' : ''}</p>`;
  }

  async function openWrapped(period) {
    let w;
    try { w = await getJSON(`api/wrapped?period=${encodeURIComponent(period)}`); } catch (err) { toast(err.message); return; }
    state.wrappedData = w;
    if (!w.planes) { openOverlay(`<div class="wrapped"><h2>${esc(w.name)} in planes</h2><p class="muted">Nothing spotted yet.</p></div>`); return; }
    const big = (k, v, note = '') => `<div class="wt"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div>${note ? `<div class="n">${esc(note)}</div>` : ''}</div>`;
    const tips = w.chart.map((n, i) => `${w.year ? w.ticks[i] : `${w.ticks[i]} ${w.name.split(' ')[0]}`} · ${n} plane${n === 1 ? '' : 's'}`);
    const ticks = w.year ? w.ticks.map((t, i) => [i, t]).filter(([i]) => i % 2 === 0)
      : w.ticks.map((t, i) => [i, t]).filter(([i]) => i % 7 === 0);
    openOverlay(`<div class="wrapped">
      <div class="k">✈ planespotter</div><h2>${esc(w.name)} in planes</h2>
      <div class="wgrid">
        ${big('Planes over you', w.planes.toLocaleString())}
        ${big('Airlines', w.airlines, w.new_airlines ? `${w.new_airlines} new` : '')}
        ${big('Aircraft types', w.types, w.new_types ? `${w.new_types} new` : '')}
        ${big('Countries', w.countries)}
        ${w.top_airline ? big('Top airline', w.top_airline[0], `${w.top_airline[1]} flights`) : ''}
        ${w.top_type ? big('Top aircraft', w.top_type[0], `${w.top_type[1]} times`) : ''}
        ${w.top_route ? big('Busiest route', w.top_route[0], `${w.top_route[1]} flights`) : ''}
        ${w.farthest ? big('Came the farthest', `${w.farthest.city} (${w.farthest.code})`, `${w.farthest.miles.toLocaleString()} mi · ${w.farthest.label}`) : ''}
        ${w.rarest ? big('Rarest', w.rarest.aircraft, `${w.rarest.label} · seen ${w.rarest.times}×`) : ''}
        ${big('Busiest day', new Date(`${w.busiest_day.date}T12:00:00`).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' }), `${w.busiest_day.count} planes`)}
        ${big('Favourite hour', `${String(w.favourite_hour).padStart(2, '0')}:00`)}
        ${big('Old friend', w.regular.label, `${w.regular.times} visits`)}
        ${w.lowest ? big('Lowest', w.lowest.alt, w.lowest.label) : ''}
        ${big('At night', w.night)}
        ${big('Seen with your own eyes', w.eyes)}
        ${w.quiz.answered ? big('Guess the plane', `${w.quiz.right} / ${w.quiz.answered}`) : big('Your photos', w.photos)}
      </div>
      <h3>${w.year ? 'Planes by month' : 'Planes by day'}</h3>
      ${columnChart(w.chart, tips, ticks)}
      ${w.offline ? `<p class="muted">Includes ${w.offline} found while planespotter was off 📴</p>` : ''}
      <div class="overlay-actions" style="margin-top:12px"><button class="btn" data-action="share-wrapped">↗ Share</button></div>
    </div>`, true);
  }

  async function shareWrapped() {
    const w = state.wrappedData;
    if (!w) return;
    const W = 1080, H = 1350, ACC = '#ffb020', MUTED = '#9aa7b6';
    const c = document.createElement('canvas'); c.width = W; c.height = H;
    const g = c.getContext('2d');
    const bg = g.createLinearGradient(0, 0, 0, H); bg.addColorStop(0, '#101826'); bg.addColorStop(1, '#070a0e');
    g.fillStyle = bg; g.fillRect(0, 0, W, H);
    fitText(g, `${w.name} in planes`, 80, 150, 84, 800, W - 160, '#ffffff');
    fitText(g, `${w.planes.toLocaleString()}`, 80, 330, 200, 800, W - 160, ACC);
    fitText(g, 'planes flew over my house', 80, 400, 44, 600, W - 160, '#e8edf3');
    const rows = [
      ['Airlines', String(w.airlines)], ['Aircraft types', String(w.types)], ['Countries', String(w.countries)],
      w.top_airline ? ['Top airline', w.top_airline[0]] : null, w.top_type ? ['Top aircraft', w.top_type[0]] : null,
      w.farthest ? ['Came the farthest', `${w.farthest.city} · ${w.farthest.miles.toLocaleString()} mi`] : null,
      w.rarest ? ['Rarest', w.rarest.aircraft] : null,
    ].filter(Boolean);
    rows.forEach(([k, v], i) => {
      const y = 520 + i * 105;
      g.font = `700 26px ${CARD_FONT}`; g.fillStyle = MUTED; g.fillText(k.toUpperCase(), 80, y);
      fitText(g, v, 80, y + 50, 46, 700, W - 160, '#ffffff');
    });
    const max = Math.max(1, ...w.chart), bw = (W - 160) / w.chart.length;
    w.chart.forEach((n, i) => {
      const h = (n / max) * 120;
      g.fillStyle = ACC; roundRect(g, 80 + i * bw + 2, 1260 - h, Math.max(2, bw - 4), Math.max(2, h), 4); g.fill();
    });
    g.font = `800 28px ${CARD_FONT}`; g.fillStyle = MUTED; g.fillText('planespotter', W - 80 - g.measureText('planespotter').width, 1312);
    const blob = await new Promise((r) => c.toBlob(r, 'image/png'));
    const file = new File([blob], `${w.name} in planes.png`, { type: 'image/png' });
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      try { await navigator.share({ files: [file], title: `${w.name} in planes` }); return; } catch (err) { if (err.name === 'AbortError') return; }
    }
    const url = URL.createObjectURL(blob);
    openOverlay(`<img class="share-card" data-blob="1" src="${url}" alt="">
      <div class="overlay-row"><div class="muted">Save the picture, then send it however you like.</div>
      <div class="overlay-actions"><a class="btn" href="${url}" download="${esc(file.name)}">Download</a></div></div>`);
  }

  async function openProfile(hex) {
    let p;
    try { p = await getJSON(`api/plane?hex=${encodeURIComponent(hex)}`); } catch (err) { toast('You haven’t spotted this plane over your zone yet.'); return; }
    openOverlay(`<div class="profile">
      ${p.photo_url ? `<img class="big-photo" src="${esc(p.photo_url)}" alt="">` : ''}
      <div class="title-row" style="margin-top:10px">${logo(p.callsign, true)}<div><h2>${esc(p.registration || p.hex.toUpperCase())}</h2>
        <div class="muted">${esc([p.aircraft, p.airline || p.owner].filter(Boolean).join(' · '))}${p.country ? ` · ${p.country.flag} ${esc(p.country.name)}` : ''}</div></div></div>
      <div class="stats">${stat('Seen', `${p.times} time${p.times === 1 ? '' : 's'}`)}${stat('First', new Date(p.first_seen * 1000).toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' }))}${stat('Usually', `${String(p.usual_hour).padStart(2, '0')}:00`)}</div>
      ${p.routes.length ? `<h3>Routes</h3>${p.routes.map(([r, n]) => `<div class="crow"><span class="cname">${esc(r)}</span><span class="cnum">${n}</span></div>`).join('')}` : ''}
      <h3>Every visit</h3>${p.visits.map((v) => `<button class="crow flight" data-open-flight="${v.id}"><span class="cname"><strong>${esc(shortWhen(v.when))}</strong>
        <span class="muted">${esc([v.callsign, v.route].filter(Boolean).join(' · '))}${v.offline ? ' · 📴' : ''}</span></span><span class="cnum">${esc(v.alt)}</span></button>`).join('')}
    </div>`, true);
  }

  // ---------- kiosk mode: a big, hands-off display for an old tablet or a TV ----------
  const kiosk = location.pathname.endsWith('/kiosk') || new URLSearchParams(location.search).has('kiosk');

  function renderKiosk(now) {
    if (!kiosk || !state.config) return;
    $('#kiosk-clock').textContent = new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    const next = predictions(now, 900).slice(0, 4);
    const el = $('#kiosk-next');
    const sat = satTarget();
    let html = sat ? `<div class="kn sat"><span class="who">🛰️ ${esc(sat.name)}</span><span class="t">look ${compass(sat.az)}, ${Math.round(sat.el)}° up</span></div>` : '';
    html += next.length ? next.map(({ p, eta, az }) => {
      const info = { ...(infoFor(p) || {}), ...(checkedRoute(p.hex) || {}) };
      return `<div class="kn">${logo(p.callsign, true)}<span class="who"><strong>${esc(label(p))}</strong>
        <span class="r">${esc([routeShort(info), info.airline].filter(Boolean).join(' · '))}</span></span>
        <span class="t${eta === 0 ? ' now' : ''}">${eta === 0 ? 'overhead' : countdown(eta)}<small>${compass(az)}</small></span></div>`;
    }).join('') : '<div class="kn quiet">No planes due in the next 15 minutes</div>';
    if (state.flow && state.flow.text) html += `<div class="kflow">🛬 ${esc(state.flow.text)}</div>`;
    if (html !== el.dataset.html) { el.innerHTML = html; el.dataset.html = html; }
  }

  function startKiosk() {
    document.body.classList.add('kiosk');
    $('#kiosk-clock').hidden = false;
    $('#kiosk-next').hidden = false;
    // Keep the screen on, where the browser allows it.
    const lock = () => navigator.wakeLock && navigator.wakeLock.request('screen').catch(() => {});
    lock();
    document.addEventListener('visibilitychange', () => { if (!document.hidden) lock(); });
    setInterval(() => { if (!state.selected) goHome(); }, 5 * 60000);
    setTimeout(() => { if (map) map.resize(); goHome(); }, 600);
  }

  // ---------- replay videos ----------
  let recorder = null;
  function recordReplay() {
    if (recorder) { recorder.stop(); return; }
    const src = map.getCanvas();
    const types = ['video/mp4;codecs=avc1', 'video/mp4', 'video/webm;codecs=vp9', 'video/webm'];
    const type = window.MediaRecorder && types.find((t) => MediaRecorder.isTypeSupported(t));
    if (!type) { toast('This browser can’t record video. Try Chrome or Safari.'); return; }
    // Draw each frame of the map onto a canvas of our own, with the time and a title on it.
    const out = document.createElement('canvas');
    out.width = Math.round(src.width / 2) * 2; out.height = Math.round(src.height / 2) * 2;
    const g = out.getContext('2d');
    const scale = out.width / src.clientWidth;
    const draw = () => {
      g.drawImage(src, 0, 0, out.width, out.height);
      const r = state.replay;
      g.fillStyle = 'rgba(10,14,20,.78)';
      roundRect(g, 16 * scale, 16 * scale, 240 * scale, 64 * scale, 12 * scale); g.fill();
      g.fillStyle = '#fff'; g.font = `800 ${30 * scale}px ${CARD_FONT}`; g.fillText(clockOf(r.t), 30 * scale, 58 * scale);
      g.fillStyle = '#ffb020'; g.font = `700 ${14 * scale}px ${CARD_FONT}`;
      g.fillText(new Date(`${r.day}T12:00:00`).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' }), 140 * scale, 42 * scale);
      g.fillText('planespotter', 140 * scale, 64 * scale);
    };
    map.on('render', draw);
    const chunks = [];
    recorder = new MediaRecorder(out.captureStream(30), { mimeType: type, videoBitsPerSecond: 6e6 });
    recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    recorder.onstop = () => {
      map.off('render', draw);
      recorder = null;
      $('#replay-record').textContent = '🎬 Save video';
      state.replay.playing = false;
      updateReplayBar();
      const blob = new Blob(chunks, { type: type.split(';')[0] });
      const url = URL.createObjectURL(blob);
      const name = `planespotter-replay-${state.replay.day}.${type.includes('mp4') ? 'mp4' : 'webm'}`;
      openOverlay(`<video class="share-card" src="${url}" controls playsinline autoplay muted loop></video>
        <div class="overlay-row"><div class="muted">${(blob.size / 1e6).toFixed(1)} MB</div>
        <div class="overlay-actions"><a class="btn" href="${url}" download="${name}">Download</a></div></div>`, true);
    };
    recorder.start(1000);
    $('#replay-record').textContent = '⏹ Stop';
    // Record from the first plane of the day to the end, at the chosen speed.
    const r = state.replay;
    const firsts = r.flights.map((f) => f.ts[0]);
    r.t = firsts.length ? Math.max(0, Math.min(...firsts) - 300) : 0;
    r.playing = true;
    const lastT = r.flights.length ? Math.max(...r.flights.map((f) => f.ts[f.ts.length - 1])) + 300 : 86400;
    const check = setInterval(() => {
      map.triggerRepaint();
      if (!recorder) { clearInterval(check); return; }
      if (state.replay.t >= Math.min(86400, lastT) || !state.replay.on) { clearInterval(check); recorder.stop(); }
    }, 250);
  }

  // ---------- day replay ----------
  const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const clockOf = (secs) => `${String(Math.floor(secs / 3600) % 24).padStart(2, '0')}:${String(Math.floor(secs / 60) % 60).padStart(2, '0')}`;

  function openReplay() {
    const r = state.replay;
    r.on = true;
    const daySelect = $('#replay-day');
    daySelect.innerHTML = Array.from({ length: 7 }, (_, i) => {
      const d = new Date(); d.setDate(d.getDate() - i);
      return `<option value="${isoDay(d)}">${i === 0 ? 'Today' : i === 1 ? 'Yesterday' : d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })}</option>`;
    }).join('');
    $('#replay').hidden = false;
    $('#map-wrap').classList.add('replaying');
    $('#btn-replay').setAttribute('aria-pressed', 'true');
    if (state.selected) select(null);
    loadReplay(daySelect.value);
  }

  function closeReplay() {
    Object.assign(state.replay, { on: false, playing: false });
    $('#replay').hidden = true;
    $('#map-wrap').classList.remove('replaying');
    $('#btn-replay').setAttribute('aria-pressed', 'false');
    render();
  }

  async function loadReplay(day) {
    const r = state.replay;
    r.day = day;
    r.playing = false;
    $('#replay-note').textContent = 'Loading…';
    let data;
    try { data = await getJSON(`api/replay?day=${day}`); } catch { $('#replay-note').textContent = 'Couldn’t load that day.'; return; }
    r.flights = data.flights.map((f) => ({
      ...f,
      ts: f.path.map((q) => q[3]),
      p3: f.path.map((q) => [q[0], q[1], (q[2] || 0) * FT]),
      p2: f.path.map((q) => [q[0], q[1], 0]),
    }));
    // Start a few minutes before the first plane of the day.
    const firsts = r.flights.map((f) => f.first_seen - data.start);
    r.t = firsts.length ? Math.max(0, Math.min(...firsts) - 600) : 0;
    r.playing = r.flights.length > 0;
    updateReplayBar();
  }

  /** Where each replayed plane is at time t (seconds after midnight), and which way it's heading. */
  function replayPlanes(t, is3d) {
    const out = [];
    for (const f of state.replay.flights) {
      const ts = f.ts;
      if (t < ts[0] || t > ts[ts.length - 1]) continue;
      let lo = 0, hi = ts.length - 1;
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (ts[mid] <= t) lo = mid; else hi = mid; }
      const a = f.path[lo], b = f.path[hi];
      const k = ts[hi] > ts[lo] ? (t - ts[lo]) / (ts[hi] - ts[lo]) : 0;
      const alt = (a[2] || 0) + ((b[2] || 0) - (a[2] || 0)) * k;
      out.push({
        f, lon: a[0] + (b[0] - a[0]) * k, lat: a[1] + (b[1] - a[1]) * k, z: is3d ? alt * FT : 0,
        heading: bearingTo(a[1], a[0], b[1], b[0]),
      });
    }
    return out;
  }

  function updateReplayBar() {
    const r = state.replay;
    $('#replay-slider').value = String(Math.round(r.t));
    $('#replay-clock').textContent = clockOf(r.t);
    $('#replay-play').textContent = r.playing ? '❚❚' : '▶';
    $('#replay-play').setAttribute('aria-label', r.playing ? 'Pause' : 'Play');
    const flying = replayPlanes(r.t, false).length;
    const total = r.flights.length;
    $('#replay-note').textContent = total
      ? `${flying} of this day's ${total} spotted flight${total === 1 ? '' : 's'} in the air`
      : 'No planes were spotted on this day.';
  }

  function replayLayers(is3d) {
    const r = state.replay;
    const now = replayPlanes(r.t, is3d);
    return [
      new deck.TripsLayer({
        id: 'replay-trails', data: r.flights, getPath: (f) => (is3d ? f.p3 : f.p2), getTimestamps: (f) => f.ts,
        getColor: a(C.accent, 230), widthMinPixels: 2.5, trailLength: 1800, fadeTrail: true,
        currentTime: r.t, capRounded: true, jointRounded: true, updateTriggers: { getPath: is3d },
      }),
      new deck.IconLayer({
        id: 'replay-planes', data: now, ...PLANE_ICON, getIcon: () => 'plane',
        getPosition: (d) => [d.lon, d.lat, d.z], getAngle: (d) => -d.heading, getSize: 26,
        sizeUnits: 'pixels', billboard: false, getColor: a(C.accent, 255),
      }),
      map.getZoom() >= 8 ? new deck.TextLayer({
        id: 'replay-labels', data: now, getPosition: (d) => [d.lon, d.lat, d.z], getText: (d) => d.f.callsign,
        getPixelOffset: [0, -22], getSize: 11, getColor: a(C.ink, 255), fontWeight: 600,
        background: true, getBackgroundColor: a(C.panel, 200), backgroundPadding: [5, 2],
        fontFamily: '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif',
      }) : null,
    ];
  }

  /** Your zone and your house: drawn in every mode. */
  function baseLayers() {
    const { home, radius_miles: radius } = state.config;
    return [
      new deck.PolygonLayer({
        id: 'zone', data: [{ polygon: state.zone }], getPolygon: (d) => d.polygon,
        filled: true, stroked: true, getFillColor: a(C.accent, dark ? 20 : 26), getLineColor: a(C.accent, 230),
        lineWidthUnits: 'pixels', getLineWidth: 2, parameters: GROUND,
      }),
      new deck.TextLayer({
        id: 'zone-label', data: [{ pos: destination(home.lat, home.lon, 0, radius * MI), text: `${radius} mi` }],
        getPosition: (d) => d.pos, getText: (d) => d.text, getSize: 12, getColor: a(C.accent, 255),
        fontWeight: 700, getPixelOffset: [0, -10], background: true, getBackgroundColor: a(C.panel, 210),
        backgroundPadding: [5, 2], fontFamily: '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif',
      }),
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
    ];
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

    if (state.replay.on) {
      overlay.setProps({ layers: [...baseLayers(), ...replayLayers(is3d)].filter(Boolean) });
      return;
    }

    const base = baseLayers();
    const layers = [
      base[0],
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
      base[1],

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
      ...base.slice(2),

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
  let lastReplayBar = 0;
  let lastLookUp = 0;
  let lastUpNextAt = 0;
  let lastMeterAt = 0;
  let lastKioskAt = 0;
  function animate(ts) {
    if (!document.hidden && ts - lastFrame > 50) {
      const r = state.replay;
      if (r.on && r.playing) {
        r.t = Math.min(86400, r.t + ((ts - lastFrame) / 1000) * r.speed);
        if (r.t >= 86400) r.playing = false;
        if (ts - lastReplayBar > 250) { updateReplayBar(); lastReplayBar = ts; }
      }
      render();
      if (ts - lastLookUp > 500 && state.config) { renderLookUp(performance.now()); lastLookUp = ts; }
      if (ts - lastUpNextAt > 1000 && state.config) { renderUpNext(performance.now()); lastUpNextAt = ts; }
      if (meter.on && state.tab === 'noise' && ts - lastMeterAt > 250) { renderMeter(); lastMeterAt = ts; }
      if (kiosk && ts - lastKioskAt > 1000) { renderKiosk(performance.now()); lastKioskAt = ts; }
      lastFrame = ts;
    }
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
    $('#tools').hidden = tab === 'collection' || tab === 'noise';
    state.listLimit = LIST_PAGE;
    $('#list').scrollTop = 0;
    renderList();
    renderUpNext(performance.now());
    if (tab === 'history') loadFlights();
    if (tab === 'collection') { loadCollection(); loadYours(); }
    if (tab === 'noise') loadNoise();
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
      const open = e.target.closest('[data-open-flight]');
      if (open) { openFlight(open.dataset.openFlight); return; }
      const wr = e.target.closest('[data-wrapped]');
      if (wr) { openWrapped(wr.dataset.wrapped); return; }
      const prof = e.target.closest('[data-profile]');
      if (prof) { openProfile(prof.dataset.profile); return; }
      const view = e.target.closest('[data-view-photo]');
      if (view) { viewPhoto(view.dataset.viewPhoto); return; }
      if (e.target.closest('[data-action="meter"]')) { if (meter.on) stopMeter(); else startMeter(); return; }
      const nd = e.target.closest('[data-noise-days]');
      if (nd) { state.noiseDays = Number(nd.dataset.noiseDays); saved.set('noiseDays', state.noiseDays); loadNoise(); return; }
      const all = e.target.closest('[data-show-all]');
      if (all) { state.collectionAll = { ...(state.collectionAll || {}), [all.dataset.showAll]: true }; renderList(); return; }
      if (e.target.closest('[data-action="more"]')) { state.listLimit += LIST_PAGE; renderList(); return; }
      const item = e.target.closest('.item');
      if (item) select(item.dataset.kind, item.dataset.key);
    });
    $('#list').addEventListener('mousemove', (e) => {
      const slot = e.target.closest('.slot');
      const chart = e.target.closest('.chart');
      for (const tip of document.querySelectorAll('.chart-tip')) if (!chart || !chart.contains(tip)) tip.hidden = true;
      if (!chart) return;
      const tip = chart.querySelector('.chart-tip');
      for (const s of chart.querySelectorAll('.slot.on')) s.classList.remove('on');
      if (!slot) { tip.hidden = true; return; }
      slot.classList.add('on');
      const box = chart.getBoundingClientRect();
      tip.textContent = slot.dataset.tip;
      tip.hidden = false;
      tip.style.left = `${Math.min(box.width - tip.offsetWidth, Math.max(0, e.clientX - box.left - tip.offsetWidth / 2))}px`;
    });
    $('#list').addEventListener('mouseleave', () => {
      for (const tip of document.querySelectorAll('.chart-tip')) tip.hidden = true;
    });
    $('#detail').addEventListener('click', (e) => {
      if (e.target.closest('.close')) select(null);
      const view = e.target.closest('[data-view-photo]');
      if (view) viewPhoto(view.dataset.viewPhoto);
      if (e.target.closest('[data-action="add-photo"]')) pickPhoto();
      if (e.target.closest('[data-action="share"]')) shareSelected();
      const thing = selectedThing();
      const t = thing && (thing.plane || thing.flight);
      if (t && e.target.closest('[data-action="follow"]')) toggleFollow(t.hex, t.callsign);
      if (t && e.target.closest('[data-action="profile"]')) openProfile(t.hex);
      const saw = e.target.closest('[data-action="saw"]');
      if (saw) {
        const thing = selectedThing();
        const t = thing && (thing.plane || thing.flight);
        if (t) toggleSaw(t.hex, t.callsign, Number(saw.dataset.at));
      }
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
    $('#btn-chime').addEventListener('click', () => setChime(!state.chime));
    $('#btn-voice').addEventListener('click', () => setVoice(!state.voice));
    setVoice(state.voice, false);
    // Browsers only start speaking after a tap or click on the page: the first one unlocks it.
    document.addEventListener('pointerdown', () => { if (state.voice && canSpeak) say(' '); }, { once: true });
    $('#btn-ar').addEventListener('click', () => {
      const secure = state.config && state.config.secure_url;
      // Phones only allow the camera and compass on a secure (https) page.
      if (window.isSecureContext || !secure) window.open('sky', '_blank');
      else window.open(`${secure}/sky`, '_blank');
    });
    $('#upnext').addEventListener('click', (e) => {
      const b = e.target.closest('[data-hex]');
      if (b) select('live', b.dataset.hex, { scroll: true });
    });
    $('#lookup').addEventListener('click', (e) => {
      const g = e.target.closest('[data-guess]');
      if (g) { e.stopPropagation(); startQuiz(g.dataset.guess); return; }
      const b = e.target.closest('.saw');
      if (b) { e.stopPropagation(); toggleSaw(b.dataset.saw, b.dataset.callsign, Date.now() / 1000); }
    });
    $('#photo-input').addEventListener('change', (e) => uploadPhoto(e.target.files[0]));
    $('#overlay').addEventListener('click', (e) => {
      if (e.target === e.currentTarget || e.target.closest('[data-action="close-overlay"]')) { closeOverlay(); return; }
      const ans = e.target.closest('[data-quiz-answer]');
      if (ans) { answerQuiz(ans.dataset.quizAnswer); return; }
      if (e.target.closest('[data-action="share-wrapped"]')) { shareWrapped(); return; }
      const del = e.target.closest('[data-delete-photo]');
      if (del) { deletePhoto(del.dataset.deletePhoto); return; }
      const open = e.target.closest('[data-open-flight]');
      if (open) openFlight(open.dataset.openFlight);
    });
    if (state.chime) { $('#btn-chime').setAttribute('aria-pressed', 'true'); $('#btn-chime').textContent = '🔔 Chime on'; }
    $('#btn-replay').addEventListener('click', () => (state.replay.on ? closeReplay() : openReplay()));
    $('#replay-close').addEventListener('click', () => { if (recorder) recorder.stop(); closeReplay(); });
    $('#replay-record').addEventListener('click', recordReplay);
    $('#place').addEventListener('change', (e) => {
      const proto = location.protocol === 'https:' ? 'http:' : location.protocol;  // other places' maps are plain http
      location.href = `${proto}//${location.hostname}:${e.target.value}/`;
    });
    $('#replay-play').addEventListener('click', () => {
      const r = state.replay;
      if (r.t >= 86400) r.t = 0;
      r.playing = !r.playing;
      updateReplayBar();
    });
    $('#replay-day').addEventListener('change', (e) => loadReplay(e.target.value));
    $('#replay-speed').addEventListener('change', (e) => { state.replay.speed = Number(e.target.value); });
    $('#replay-slider').addEventListener('input', (e) => {
      state.replay.t = Number(e.target.value);
      state.replay.playing = false;
      updateReplayBar();
    });
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
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      if (!$('#overlay').hidden) closeOverlay();
      else if (state.selected) select(null);
    });
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

    // Opened from a phone notification: ?flight=<id> (a spotted flight) or ?plane=<hex> (live).
    const params = new URLSearchParams(location.search);
    if (params.get('flight')) { state.pendingSelect = { kind: 'history', key: params.get('flight') }; state.tab = 'history'; }
    else if (params.get('plane')) state.pendingSelect = { kind: 'live', key: params.get('plane').toLowerCase() };
    setTab(state.tab);
    pollLive();
    if (state.tab !== 'history') loadFlights();
    setInterval(() => { if (!document.hidden) loadWholeAll(); }, 30000);
    setInterval(() => { if (!document.hidden && state.tab === 'collection') loadCollection(); }, 120000);
    setInterval(() => { if (!document.hidden) loadFlights(); }, HISTORY_EVERY_MS);
    loadSky();
    setInterval(() => { if (!document.hidden) loadSky(); }, 5 * 60000);
    loadFlow();
    setInterval(() => { if (!document.hidden) loadFlow(); }, 5 * 60000);
    loadToday();
    setInterval(() => { if (!document.hidden) loadToday(); }, 2 * 60000);
    loadSpace();
    setInterval(() => { if (!document.hidden) loadSpace(); }, 60000);
    if ((state.config.places || []).length) {
      const port = Number(location.port || (location.protocol === 'https:' ? 443 : 80));
      const sel = $('#place');
      sel.innerHTML = state.config.places.map((pl) => `<option value="${pl.port}"${pl.port === port || (pl.name === home.name) ? ' selected' : ''}>${esc(pl.name)}</option>`).join('');
      sel.hidden = false;
    }
    if (kiosk) startKiosk();
    loadYours();
    setInterval(() => { if (!document.hidden && state.tab === 'noise') loadNoise(); }, 120000);
    renderHeatPanel();
    loadHeat();
    setInterval(() => { if (!document.hidden) loadHeat(); }, HISTORY_EVERY_MS);
    // Keep the selected flight's whole path up to date (a live plane keeps flying).
    setInterval(() => { if (!document.hidden && state.selected) loadWhole(); }, HISTORY_EVERY_MS);
    requestAnimationFrame(animate);
  }

  start();
})();

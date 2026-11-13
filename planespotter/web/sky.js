/* planespotter sky view: point your phone at the sky and every plane is labelled where it is.
 *
 * The phone's compass and tilt sensors say which way the camera is pointing; each plane's
 * direction from home (compass bearing and angle above the horizon) is projected onto the
 * screen like a camera would. Without sensors (a computer), drag to look around.
 */
(() => {
  'use strict';

  const FT = 0.3048, MI = 1609.344, KT = 0.514444, EARTH_M = 6371008.8;
  const R = Math.PI / 180;
  const $ = (s) => document.querySelector(s);
  const store = {
    get(k, d) { try { const v = localStorage.getItem(`planespotter.sky.${k}`); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(`planespotter.sky.${k}`, JSON.stringify(v)); } catch { /* private mode */ } },
  };
  const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
  const compass = (deg) => COMPASS[Math.round((((deg % 360) + 360) % 360) / 45) % 8];
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const label = (p) => p.callsign || p.registration || p.hex.toUpperCase();

  const view = {
    sensors: false,          // true once the compass/tilt sensors are giving readings
    alpha: 0, beta: 90, gamma: 0, // device orientation (degrees)
    iosOffset: null,         // iOS reports a relative alpha: the correction that makes it point north
    nudge: store.get('nudge', 0),  // your own compass correction (drag sideways), degrees
    fov: store.get('fov', 62),     // the camera's field of view across the long side of the screen
    yaw: 180, pitch: 12,     // where you're looking when dragging instead (computers)
  };
  const state = { config: null, planes: [], info: new Map(), sun: null, selected: null, eyes: [], hits: [], tracks: [] };

  async function getJSON(url) {
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  // ---------- where things are ----------
  function destination(lat, lon, bearing, metres) {
    const d = metres / EARTH_M, b = bearing * R, p1 = lat * R, l1 = lon * R;
    const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b));
    const l2 = l1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
    return [p2 / R, l2 / R];
  }
  function milesBetween(lat1, lon1, lat2, lon2) {
    const dp = (lat2 - lat1) * R, dl = (lon2 - lon1) * R;
    const h = Math.sin(dp / 2) ** 2 + Math.cos(lat1 * R) * Math.cos(lat2 * R) * Math.sin(dl / 2) ** 2;
    return 3958.8 * 2 * Math.asin(Math.min(1, Math.sqrt(h)));
  }
  function bearingTo(lat1, lon1, lat2, lon2) {
    const dl = (lon2 - lon1) * R;
    const y = Math.sin(dl) * Math.cos(lat2 * R);
    const x = Math.cos(lat1 * R) * Math.sin(lat2 * R) - Math.sin(lat1 * R) * Math.cos(lat2 * R) * Math.cos(dl);
    return (Math.atan2(y, x) / R + 360) % 360;
  }

  /** A direction in the sky as a unit vector: x east, y north, z up. */
  const dirVec = (az, el) => [Math.sin(az * R) * Math.cos(el * R), Math.cos(az * R) * Math.cos(el * R), Math.sin(el * R)];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

  /** Where each plane is in the sky right now (gliding along between updates). */
  function skyPlanes(now) {
    const { home, home_elevation_ft: groundFt = 0 } = state.config;
    return state.planes.map((p) => {
      const dt = Math.min(Math.max((now - p.fixAt) / 1000, 0), 300);
      const [lat, lon] = p.speed_kt && p.heading != null ? destination(p.lat, p.lon, p.heading, p.speed_kt * KT * dt) : [p.lat, p.lon];
      const miles = milesBetween(home.lat, home.lon, lat, lon);
      const ground = miles * MI;
      // Height above you, less the curve of the Earth (planes far away sit lower than you'd think).
      const height = ((p.alt_ft || 0) - groundFt) * FT - (ground * ground) / (2 * EARTH_M);
      return { p, miles, az: bearingTo(home.lat, home.lon, lat, lon), el: Math.atan2(height, Math.max(1, ground)) / R };
    });
  }

  // ---------- which way the camera points ----------
  /** The camera's forward, right and up directions (x east, y north, z up). */
  function cameraBasis() {
    if (!view.sensors) {
      const f = dirVec(view.yaw, view.pitch);
      const right = [Math.cos(view.yaw * R), -Math.sin(view.yaw * R), 0];
      const up = [right[1] * f[2] - right[2] * f[1], right[2] * f[0] - right[0] * f[2], right[0] * f[1] - right[1] * f[0]];
      return { f, right, up };
    }
    const a = (view.alpha + (view.iosOffset || 0) - view.nudge) * R, b = view.beta * R, g = view.gamma * R;
    const cX = Math.cos(b), sX = Math.sin(b), cY = Math.cos(g), sY = Math.sin(g), cZ = Math.cos(a), sZ = Math.sin(a);
    // The device's axes in the world (W3C device orientation, Z-X'-Y'' rotations).
    const xDev = [cZ * cY - sZ * sX * sY, cY * sZ + cZ * sX * sY, -cX * sY];
    const yDev = [-cX * sZ, cZ * cX, sX];
    const zDev = [cY * sZ * sX + cZ * sY, sZ * sY - cZ * cY * sX, cX * cY];
    const turn = ((screen.orientation && screen.orientation.angle) || window.orientation || 0) * R;
    const c = Math.cos(turn), s = Math.sin(turn);
    return {
      f: zDev.map((v) => -v),  // the back camera looks out of the back of the phone
      right: xDev.map((v, i) => c * v - s * yDev[i]),
      up: yDev.map((v, i) => c * v + s * xDev[i]),
    };
  }

  const azOf = (v) => (Math.atan2(v[0], v[1]) / R + 360) % 360;
  const elOf = (v) => Math.asin(Math.max(-1, Math.min(1, v[2]))) / R;

  function onOrientation(e) {
    if (e.alpha == null || e.beta == null) return;
    view.alpha = e.alpha; view.beta = e.beta; view.gamma = e.gamma || 0;
    if (typeof e.webkitCompassHeading === 'number') {
      // iPhone: alpha starts wherever the phone was pointing, so line it up with the compass.
      const saved = view.iosOffset; view.iosOffset = 0;
      const f = cameraBasis().f;
      view.iosOffset = saved;
      if (Math.abs(f[2]) < 0.9) {  // the compass is unreliable pointing straight up or down
        const want = azOf(f) + view.nudge - e.webkitCompassHeading;  // f was worked out with the nudge in it
        if (view.iosOffset == null) view.iosOffset = want;
        else {
          let diff = ((want - view.iosOffset + 540) % 360) - 180;
          view.iosOffset += diff * 0.1;  // smooth out the compass's jitter
        }
      }
      if (view.iosOffset == null) return;
    }
    if (!view.sensors) { view.sensors = true; $('#hint').textContent = 'Labels off to one side? Drag sideways to line them up.'; }
  }

  // ---------- drawing ----------
  const canvas = $('#sky');
  const g = canvas.getContext('2d');
  let W = 0, H = 0, DPR = 1;
  function resize() {
    DPR = Math.min(2, window.devicePixelRatio || 1);
    W = window.innerWidth; H = window.innerHeight;
    canvas.width = Math.round(W * DPR); canvas.height = Math.round(H * DPR);
  }

  function project(v, cam, focal) {
    const z = dot(v, cam.f);
    if (z <= 0.02) return null;
    return [W / 2 + (focal * dot(v, cam.right)) / z, H / 2 - (focal * dot(v, cam.up)) / z, z];
  }

  function pill(x, y, lines, color, selected) {
    g.font = '700 14px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    const w1 = g.measureText(lines[0]).width;
    g.font = '500 12px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    const w2 = lines[1] ? g.measureText(lines[1]).width : 0;
    const w = Math.max(w1, w2) + 16, h = lines[1] ? 40 : 24;
    const bx = Math.min(W - w - 4, Math.max(4, x - w / 2)), by = y - h - 16;
    g.fillStyle = selected ? 'rgba(255,92,154,.92)' : 'rgba(10,14,20,.78)';
    g.strokeStyle = color; g.lineWidth = 1.5;
    g.beginPath(); g.roundRect ? g.roundRect(bx, by, w, h, 8) : g.rect(bx, by, w, h); g.fill(); g.stroke();
    g.fillStyle = '#fff';
    g.font = '700 14px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    g.fillText(lines[0], bx + 8, by + 17);
    if (lines[1]) {
      g.fillStyle = '#c9d3de';
      g.font = '500 12px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
      g.fillText(lines[1], bx + 8, by + 33);
    }
    return { x: bx, y: by, w, h: h + 16 };
  }

  function draw() {
    requestAnimationFrame(draw);
    if (!state.config) return;
    g.setTransform(DPR, 0, 0, DPR, 0, 0);
    g.clearRect(0, 0, W, H);
    const cam = cameraBasis();
    const focal = Math.max(W, H) / 2 / Math.tan((view.fov / 2) * R);
    const radius = state.config.radius_miles;

    // The horizon, with compass points along it.
    g.strokeStyle = 'rgba(255,255,255,.35)'; g.lineWidth = 1.5; g.beginPath();
    let pen = false;
    for (let az = 0; az <= 360; az += 3) {
      const s = project(dirVec(az, 0), cam, focal);
      if (s) { if (pen) g.lineTo(s[0], s[1]); else g.moveTo(s[0], s[1]); pen = true; } else pen = false;
    }
    g.stroke();
    g.textAlign = 'center';
    for (let i = 0; i < 8; i++) {
      const s = project(dirVec(i * 45, 0), cam, focal);
      if (!s) continue;
      g.font = `${i % 2 ? 600 : 800} ${i % 2 ? 13 : 16}px -apple-system, BlinkMacSystemFont, sans-serif`;
      g.fillStyle = i === 0 ? '#ff6b6b' : 'rgba(255,255,255,.8)';
      g.fillText(COMPASS[i], s[0], s[1] + 20);
    }
    g.textAlign = 'left';

    // The sun (don't look at it!).
    if (state.sun && state.sun.altitude > -2) {
      const s = project(dirVec(state.sun.azimuth, state.sun.altitude), cam, focal);
      if (s) {
        g.fillStyle = 'rgba(255,214,102,.9)'; g.beginPath(); g.arc(s[0], s[1], 16, 0, 7); g.fill();
        g.fillStyle = '#fff'; g.font = '600 12px -apple-system, sans-serif'; g.fillText('Sun', s[0] + 20, s[1] + 4);
      }
    }

    // Satellites crossing the sky (the space station looks like a bright, steady star).
    const nowS = Date.now() / 1000;
    for (const tr of state.tracks) {
      const pts = tr.points;
      const i = pts.findIndex((q) => q[0] >= nowS);
      if (i < 1) continue;
      const [t0, az0, el0] = pts[i - 1], [t1, az1, el1] = pts[i];
      const k = (nowS - t0) / Math.max(1, t1 - t0);
      let daz = az1 - az0; if (daz > 180) daz -= 360; if (daz < -180) daz += 360;
      const el = el0 + (el1 - el0) * k;
      if (el < 0) continue;
      const s = project(dirVec((az0 + daz * k + 360) % 360, el), cam, focal);
      if (!s) continue;
      g.fillStyle = '#ffffff'; g.beginPath(); g.arc(s[0], s[1], 6, 0, 7); g.fill();
      pill(s[0], s[1], [`🛰️ ${tr.name}`, tr.visible ? 'bright, steady, moving fast' : 'in daylight: hard to see'], '#ffffff', false);
    }

    // The planes, farthest first so near ones end up on top.
    const now = performance.now();
    const items = skyPlanes(now).filter((d) => d.el > -2).sort((x, y) => y.miles - x.miles);
    state.hits = [];
    const offscreen = [];
    for (const d of items) {
      const v = dirVec(d.az, d.el);
      const s = project(v, cam, focal);
      const near = d.p.in_zone || d.miles <= radius * 1.5;
      const color = state.selected === d.p.hex ? '#ff5c9a' : near ? '#ffb020' : '#6cb8ff';
      if (!s || s[0] < -40 || s[0] > W + 40 || s[1] < -40 || s[1] > H + 40) {
        if (near || state.selected === d.p.hex) offscreen.push({ d, v, color });
        continue;
      }
      g.fillStyle = color; g.strokeStyle = '#000'; g.lineWidth = 2;
      g.beginPath(); g.arc(s[0], s[1], near ? 7 : 5, 0, 7); g.fill(); g.stroke();
      const info = state.info.get(d.p.hex) || {};
      const route = info.origin || info.destination ? `${info.origin || '?'}→${info.destination || '?'} · ` : '';
      const lines = d.miles < 40 || near || state.selected === d.p.hex
        ? [label(d.p), `${route}${Math.round(d.p.alt_ft || 0).toLocaleString()} ft · ${d.miles.toFixed(1)} mi`]
        : [label(d.p)];
      const box = pill(s[0], s[1], lines, color, state.selected === d.p.hex);
      state.hits.push({ hex: d.p.hex, x1: Math.min(box.x, s[0] - 14), x2: Math.max(box.x + box.w, s[0] + 14), y1: box.y, y2: s[1] + 14 });
    }

    // Arrows at the edge pointing to nearby planes you're not looking at.
    for (const { d, v, color } of offscreen.slice(-4)) {
      const x = dot(v, cam.right), y = dot(v, cam.up);
      const ang = Math.atan2(-y, x);
      const ex = W / 2 + Math.cos(ang) * (W / 2 - 34), ey = H / 2 + Math.sin(ang) * (H / 2 - 70);
      g.save(); g.translate(ex, ey); g.rotate(ang);
      g.fillStyle = color; g.beginPath(); g.moveTo(16, 0); g.lineTo(-8, -10); g.lineTo(-8, 10); g.closePath(); g.fill();
      g.restore();
      g.fillStyle = '#fff'; g.font = '700 12px -apple-system, sans-serif';
      g.textAlign = 'center'; g.fillText(label(d.p), ex - Math.cos(ang) * 26, ey - Math.sin(ang) * 26 + 4); g.textAlign = 'left';
    }

    const az = azOf(cam.f), el = elOf(cam.f);
    $('#facing').textContent = `${compass(az)} ${Math.round(az)}° · ${Math.round(el)}° up`;
  }

  // ---------- data ----------
  async function poll() {
    try {
      const data = await getJSON('api/live');
      const now = performance.now();
      const since = Math.max(0, (data.time || 0) - (data.updated || data.time || 0));
      state.planes = data.aircraft.map((p) => ({ ...p, fixAt: now - (since + (p.seen_pos || 0)) * 1000 }));
      const n = state.planes.length;
      if (!view.sensors && !hintSet) $('#hint').textContent = `${n} plane${n === 1 ? '' : 's'} nearby · drag to look around`;
      for (const p of state.planes.slice(0, 15)) lookUp(p);
      if (!view.sensors && !state.aimed && state.planes.length && state.config) {
        // Without a compass, start out looking at the nearest plane.
        const d = skyPlanes(performance.now()).sort((x, y) => x.miles - y.miles)[0];
        view.yaw = d.az;
        view.pitch = Math.max(4, Math.min(60, d.el + 4));
        state.aimed = true;
      }
      renderCard();
    } catch {
      $('#hint').textContent = 'Can’t reach planespotter. Is it still running?';
    }
    setTimeout(poll, 5000);
  }

  let infoBusy = 0;
  function lookUp(p) {
    if (state.info.has(p.hex) || infoBusy >= 2) return;
    state.info.set(p.hex, {});
    infoBusy++;
    getJSON(`api/info?${new URLSearchParams({ hex: p.hex, callsign: p.callsign || '', lat: p.lat, lon: p.lon })}`)
      .then((info) => { state.info.set(p.hex, info); renderCard(); })
      .catch(() => {})
      .finally(() => { infoBusy--; });
  }

  async function loadSky() {
    try { state.sun = (await getJSON('api/sky')).sun; } catch { /* no sun marker */ }
    try { state.tracks = (await getJSON('api/space')).tracks || []; } catch { /* no satellites */ }
  }

  // ---------- tapping a plane ----------
  const sawIt = (hex) => state.eyes.some(([h, t]) => h === hex && Math.abs(t - Date.now() / 1000) < 3600);

  function renderCard() {
    const el = $('#card');
    const p = state.planes.find((x) => x.hex === state.selected);
    if (!p) { el.hidden = true; return; }
    const info = state.info.get(p.hex) || {};
    const route = info.origin || info.destination
      ? `${info.origin_city || info.origin || '?'} → ${info.destination_city || info.destination || '?'}` : '';
    const seen = sawIt(p.hex);
    el.innerHTML = `<button class="x" data-close aria-label="Close">×</button>
      <h2>${esc(label(p))}</h2>
      <div class="sub">${esc([info.airline, info.aircraft_desc || p.type].filter(Boolean).join(' · '))}</div>
      ${route ? `<div>${esc(route)}</div>` : ''}
      <div class="sub">${Math.round(p.alt_ft || 0).toLocaleString()} ft · ${p.distance_miles.toFixed(1)} mi ${compass(p.bearing)} · ${Math.round(p.speed_kt || 0)} kt</div>
      <div class="row"><button data-saw class="${seen ? 'done' : ''}">${seen ? '✓ You saw it' : '👀 I saw it'}</button>
      <button data-map>Show on the map</button></div>`;
    el.hidden = false;
  }

  canvas.addEventListener('click', (e) => {
    if (dragged) return;
    const hit = state.hits.slice().reverse().find((h) => e.clientX >= h.x1 && e.clientX <= h.x2 && e.clientY >= h.y1 && e.clientY <= h.y2);
    state.selected = hit ? hit.hex : null;
    renderCard();
  });
  $('#card').addEventListener('click', async (e) => {
    if (e.target.closest('[data-close]')) { state.selected = null; renderCard(); return; }
    const p = state.planes.find((x) => x.hex === state.selected);
    if (!p) return;
    if (e.target.closest('[data-map]')) location.href = `./?plane=${p.hex}`;
    if (e.target.closest('[data-saw]')) {
      try {
        const res = await fetch('api/saw', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ hex: p.hex, callsign: p.callsign, undo: sawIt(p.hex) }) });
        state.eyes = (await res.json()).eyes || state.eyes;
      } catch { /* try again */ }
      renderCard();
    }
  });

  // ---------- dragging: look around (computers), or line up the compass (phones) ----------
  let drag = null, dragged = false;
  canvas.addEventListener('pointerdown', (e) => { drag = { x: e.clientX, y: e.clientY, yaw: view.yaw, pitch: view.pitch, nudge: view.nudge }; dragged = false; });
  window.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.hypot(dx, dy) > 6) dragged = true;
    const degPerPx = view.fov / Math.max(W, H);
    if (view.sensors) view.nudge = drag.nudge - dx * degPerPx;
    else {
      view.yaw = (drag.yaw - dx * degPerPx + 360) % 360;
      view.pitch = Math.max(-10, Math.min(89, drag.pitch + dy * degPerPx));
    }
  });
  window.addEventListener('pointerup', () => {
    if (drag && view.sensors) store.set('nudge', view.nudge);
    if (drag) state.aimed = true;
    drag = null;
    setTimeout(() => { dragged = false; }, 0);
  });
  const zoom = (k) => { view.fov = Math.max(20, Math.min(110, view.fov * k)); store.set('fov', view.fov); };
  $('#zoom-in').addEventListener('click', () => zoom(1 / 1.1));
  $('#zoom-out').addEventListener('click', () => zoom(1.1));
  canvas.addEventListener('wheel', (e) => { e.preventDefault(); zoom(e.deltaY > 0 ? 1.05 : 1 / 1.05); }, { passive: false });

  // ---------- starting up ----------
  async function startCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return false;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false });
      const video = $('#cam');
      video.srcObject = stream;
      video.hidden = false;
      await video.play().catch(() => {});
      return true;
    } catch { return false; }
  }

  async function startSensors() {
    const D = window.DeviceOrientationEvent;
    if (!D) return false;
    if (typeof D.requestPermission === 'function') {  // iPhone and iPad ask first
      try { if ((await D.requestPermission()) !== 'granted') return false; } catch { return false; }
    }
    if ('ondeviceorientationabsolute' in window) window.addEventListener('deviceorientationabsolute', onOrientation);
    else window.addEventListener('deviceorientation', onOrientation);
    return true;
  }

  let hintSet = false;  // a message about the camera or compass stays up instead of the plane count
  async function begin() {
    $('#go').disabled = true;
    const [camera, sensors] = await Promise.all([startCamera(), startSensors()]);
    $('#start').hidden = true;
    hintSet = !camera || !sensors;
    if (!sensors) {
      view.sensors = false;
      $('#hint').textContent = 'No compass (or it wasn’t allowed), so drag to look around.';
    }
    if (!camera) $('#hint').textContent = `No camera, so here’s the sky as a chart.${sensors ? '' : ' Drag to look around.'}`;
    if (sensors) setTimeout(() => { if (!view.sensors) $('#hint').textContent = 'No compass readings: drag to look around.'; }, 2500);
  }

  async function start() {
    resize();
    window.addEventListener('resize', resize);
    try { state.config = await getJSON('api/config'); } catch { $('#hint').textContent = 'Can’t reach planespotter. Is it still running?'; }
    if (state.config) document.title = `Sky view · ${state.config.home.name}`;
    try { state.eyes = (await getJSON('api/yours')).eyes; } catch { /* fine */ }
    if (!window.isSecureContext && state.config && state.config.secure_url) {
      const url = `${state.config.secure_url}/sky`;
      $('#start-note').innerHTML = `Phones only allow the camera and compass on a secure page: <a href="${esc(url)}">open the secure sky view</a> `
        + '(the first time, your phone will warn you about the certificate; the README shows how to get past it). Or tap Start to use it without them.';
    }
    $('#go').addEventListener('click', begin);
    if (new URLSearchParams(location.search).has('auto')) begin();
    poll();
    loadSky();
    setInterval(loadSky, 60000);
    requestAnimationFrame(draw);
  }

  start();
})();

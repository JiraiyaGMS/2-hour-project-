// planespotter for the free Scriptable app (https://scriptable.app): your home-screen widget, and Siri.
// It finds the planes coming over wherever your phone is, straight from adsb.lol, so it works on its own:
// at home, at a hotel, on mobile data, with your Mac on or off. Run from a Siri shortcut ("Run Script"),
// it works out what to say at the moment you ask. At home it also shows what planespotter on your Mac
// knows (your regular flights, the last plane over), and when you're away it tells your Mac where you
// are, so your plane alerts follow you. Tap the widget (or run it here) for a live map of the planes
// around you.

const SETTINGS = __SETTINGS__;
const AIRLINES = __AIRLINES__;  // [ICAO code, IATA code, name]
const AIRPORTS = __AIRPORTS__;  // the big ones: [IATA, ICAO, name, city, lat, lon] (the rest load when needed)

const AMBER = new Color("#ffb020");
const MUTED = new Color("#9aa7b6");
const small = config.widgetFamily === "small";
const forSiri = !config.runsInWidget && !config.runsInApp;  // run by a shortcut (Siri)
const GRACE = 90;           // a plane still counts as "coming over now" this long after its time
const HORIZON = 900;        // look this far ahead (seconds)
const RANGE_NM = 40;        // fetch planes this far around you (nautical miles)
const HOME_MILES = 5;       // this close to home counts as being at home
const NEARBY_MILES = 10;
const HOVER_KT = 40;        // slower than this inside your zone: hovering or parked, not "coming over"
const DAILY_LIMIT = 400;    // adsb.lol requests a day from this phone (adsb.lol allows about 500)
const WORDS = { N: "north", NE: "north-east", E: "east", SE: "south-east", S: "south", SW: "south-west",
                W: "west", NW: "north-west" };
let now = Date.now() / 1000;
const tick = () => { now = Date.now() / 1000; };  // the live map keeps going, so time moves on
const sleep = (seconds) => new Promise((resolve) => Timer.schedule(seconds * 1000, false, resolve));

// --- small helpers ---

const fm = FileManager.local();
const CACHE_PATH = fm.joinPath(fm.documentsDirectory(), "planespotter-cache.json");

function loadCache() {
  try {
    return fm.fileExists(CACHE_PATH) ? JSON.parse(fm.readString(CACHE_PATH)) : {};
  } catch (e) {
    return {};
  }
}

function saveCache(cache) {
  try {
    fm.writeString(CACHE_PATH, JSON.stringify(cache));
  } catch (e) {}
}

function withTimeout(promise, seconds) {
  let timer = null;
  const late = new Promise((resolve) => { timer = Timer.schedule(seconds * 1000, false, () => resolve(null)); });
  return Promise.race([promise.catch(() => null), late]).then((value) => {
    if (timer) timer.invalidate();
    return value;
  });
}

async function getJSON(url, seconds, headers) {
  try {
    const req = new Request(url);
    req.timeoutInterval = seconds;
    if (headers) req.headers = headers;
    const text = await withTimeout(req.loadString(), seconds + 1);
    if (text === null) return null;
    if (req.response && req.response.statusCode >= 500) return null;
    try {
      return JSON.parse(text);
    } catch (e) {  // ntfy can send several messages, one per line: the latest is last
      const lines = text.trim().split("\n");
      return JSON.parse(lines[lines.length - 1]);
    }
  } catch (e) {
    return null;
  }
}

function clock(at) {
  const d = new Date(at * 1000);
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

// --- geometry (the same sums planespotter does on the Mac) ---

const R_MI = 3958.8, MI = 1609.344, KT = 0.514444, FT = 0.3048;
const rad = (d) => (d * Math.PI) / 180, deg = (r) => (r * 180) / Math.PI;

function miles(lat1, lon1, lat2, lon2) {
  const p1 = rad(lat1), p2 = rad(lat2), dp = p2 - p1, dl = rad(lon2 - lon1);
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * R_MI * Math.asin(Math.min(1, Math.sqrt(a)));
}

function bearing(lat1, lon1, lat2, lon2) {
  const p1 = rad(lat1), p2 = rad(lat2), dl = rad(lon2 - lon1);
  const x = Math.sin(dl) * Math.cos(p2);
  const y = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return (deg(Math.atan2(x, y)) + 360) % 360;
}

function compass(d) {
  return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.floor((d % 360) / 45 + 0.5) % 8];
}

function offset(lat, lon, brg, mi) {
  const d = mi / R_MI, b = rad(brg), p1 = rad(lat), l1 = rad(lon);
  const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b));
  const l2 = l1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
  return [deg(p2), ((deg(l2) + 540) % 360) - 180];
}

function parsePlane(raw, ageS) {
  // Like planespotter's parse_aircraft: skip planes on the ground, without a position, or stale ones.
  if (typeof raw.lat !== "number" || typeof raw.lon !== "number") return null;
  if (raw.alt_baro === "ground" || (raw.seen_pos || 0) > 60) return null;
  if (String(raw.category || "").startsWith("C")) return null;  // airport vehicles and obstacles: not aircraft
  const alt = typeof raw.alt_baro === "number" ? raw.alt_baro : (typeof raw.alt_geom === "number" ? raw.alt_geom : null);
  const track = typeof raw.track === "number" ? raw.track : (typeof raw.true_heading === "number" ? raw.true_heading : null);
  const p = { hex: String(raw.hex || "").toLowerCase(), callsign: String(raw.flight || "").trim(),
              reg: String(raw.r || "").trim(), type: String(raw.t || "").trim(), lat: raw.lat, lon: raw.lon,
              alt: alt === null ? null : Math.trunc(alt), gs: typeof raw.gs === "number" ? raw.gs : null, track,
              vr: typeof raw.baro_rate === "number" ? raw.baro_rate : (typeof raw.geom_rate === "number" ? raw.geom_rate : null) };
  p.label = p.callsign || p.reg || p.hex.toUpperCase();
  const seconds = (raw.seen_pos || 0) + ageS;  // glide it on to now, as if it flew straight
  if (p.gs && p.track !== null && seconds > 0) {
    [p.lat, p.lon] = offset(p.lat, p.lon, p.track, (p.gs * 1.15078 * Math.min(seconds, 300)) / 3600);
  }
  return p;
}

function crossings(planes, lat, lon, groundFt) {
  // Planes over the zone now or due to cross it within HORIZON seconds, soonest first.
  const out = [];
  for (const p of planes) {
    const tooHigh = SETTINGS.maxAlt && p.alt !== null && p.alt > SETTINGS.maxAlt;
    const mi = miles(lat, lon, p.lat, p.lon);
    const az = bearing(lat, lon, p.lat, p.lon);
    let eta = null;
    if (mi <= SETTINGS.radius && !tooHigh) {
      if (p.gs !== null && p.gs >= HOVER_KT) eta = 0;  // (hovering or parked isn't coming over)
    } else if (p.gs && p.track !== null && !tooHigh) {
      const x = Math.sin(rad(az)) * mi * MI, y = Math.cos(rad(az)) * mi * MI, v = p.gs * KT;
      const vx = Math.sin(rad(p.track)) * v, vy = Math.cos(rad(p.track)) * v;
      const tc = -(x * vx + y * vy) / (v * v);
      if (tc > 0 && tc <= HORIZON && Math.hypot(x + vx * tc, y + vy * tc) / MI <= SETTINGS.radius) eta = tc;
    }
    if (eta === null) continue;
    const height = Math.max(0, ((p.alt || 0) - groundFt) * FT);
    out.push({ plane: p, eta, miles: mi, az, up: deg(Math.atan2(height, Math.max(1, mi * MI))) });
  }
  return out.sort((a, b) => a.eta - b.eta || a.miles - b.miles);
}

function sayName(callsign, label, airline, city) {
  // "Delta flight 573 from Atlanta" (or the callsign, if we don't know the airline).
  if (label.startsWith("~")) return "An unidentified plane";  // seen by radar only, with no identity
  airline = (airline || "").trim();
  for (const suffix of [" Air Lines", " Airlines", " Airways"]) {
    if (airline.endsWith(suffix)) airline = airline.slice(0, -suffix.length);
  }
  const number = callsign.length > 3 && /^[A-Za-z]{3}\d/.test(callsign) ? callsign.slice(3) : "";
  const name = airline && number ? airline + " flight " + number : (airline ? airline + ", " + label : label);
  return city ? name + " from " + city : name;
}

// --- where you are, and what's coming ---

async function myLocation(cache) {
  try {
    Location.setAccuracyToHundredMeters();
    const here = await withTimeout(Location.current(), forSiri ? 4 : 8);
    if (here && typeof here.latitude === "number") {
      cache.where = { lat: here.latitude, lon: here.longitude, at: now,
                      groundFt: typeof here.altitude === "number" ? here.altitude / FT : null };
      return { ...cache.where, fresh: true };
    }
  } catch (e) {}
  if (cache.where && now - cache.where.at < 6 * 3600) return { ...cache.where, fresh: false };
  return null;  // no idea: use home
}

async function placeName(where, cache) {
  const c = cache.place;
  if (c && miles(c.lat, c.lon, where.lat, where.lon) < 3) return c.name;
  try {
    const found = await withTimeout(Location.reverseGeocode(where.lat, where.lon), 3);
    const a = (found || [])[0] || {};
    const name = a.locality || a.subLocality || a.subAdministrativeArea || a.name;
    if (name) {
      cache.place = { lat: where.lat, lon: where.lon, name };
      return name;
    }
  } catch (e) {}
  return "where you are";
}

async function routeFor(p, cache) {
  // The airline and where it's from (adsbdb.com, remembered for half a day), if it fits the plane's path.
  if (!p.callsign) return {};
  cache.routes = cache.routes || {};
  const hit = cache.routes[p.callsign];
  if (hit && now - hit.at < (hit.r ? 12 : 1) * 3600) return hit.r || {};
  const body = await getJSON(SETTINGS.adsbdb + "/v0/callsign/" + encodeURIComponent(p.callsign), forSiri ? 3 : 5);
  if (body === null) return {};  // couldn't ask: try again next time
  const fr = (body.response && body.response.flightroute) || null;
  let r = null;
  if (fr) {
    r = { airline: (fr.airline && fr.airline.name) || "" };
    const o = fr.origin || {}, d = fr.destination || {};
    const direct = miles(o.latitude, o.longitude, d.latitude, d.longitude);
    const via = miles(o.latitude, o.longitude, p.lat, p.lon) + miles(p.lat, p.lon, d.latitude, d.longitude);
    if (isFinite(direct) && isFinite(via) && via <= direct * 1.15 + 60) {  // the route fits where it's flying
      r.origin = o.iata_code || o.icao_code || "";
      r.city = o.municipality || "";
      r.destination = d.iata_code || d.icao_code || "";
    }
  }
  cache.routes[p.callsign] = { at: now, r };
  for (const [k, v] of Object.entries(cache.routes)) if (now - v.at > 86400) delete cache.routes[k];
  return r || {};
}

function today() {
  const d = new Date(now * 1000);
  return d.getFullYear() + "-" + (d.getMonth() + 1) + "-" + d.getDate();
}

async function adsbGet(path, cache, seconds) {
  // Planes from adsb.lol (moved on to now). null if it didn't answer; {limited} once we've asked as often
  // as we should today (the widget, Siri, the map and searches all share DAILY_LIMIT).
  const day = today();
  if (!cache.usage || cache.usage.day !== day) cache.usage = { day, n: 0 };
  if (cache.usage.n >= DAILY_LIMIT) return { limited: true };
  cache.usage.n += 1;
  const body = await getJSON(SETTINGS.adsb + path, seconds || (forSiri ? 5 : 10));
  if (!body || !Array.isArray(body.ac)) return null;
  const age = Math.min(300, Math.max(0, body.now ? now - body.now / 1000 : 0));
  return { planes: body.ac.map((raw) => parsePlane(raw, age)).filter(Boolean), at: now };
}

async function fetchPlanes(where, cache) {
  // Every plane within RANGE_NM of where you are.
  return adsbGet("/v2/point/" + where.lat.toFixed(4) + "/" + where.lon.toFixed(4) + "/" + RANGE_NM, cache);
}

function lookAt(where, p, groundFt) {
  // Which way to look for a plane, and how high: {miles, az, up}.
  const mi = miles(where.lat, where.lon, p.lat, p.lon);
  const height = Math.max(0, ((p.alt || 0) - (groundFt || 0)) * FT);
  return { miles: mi, az: bearing(where.lat, where.lon, p.lat, p.lon), up: deg(Math.atan2(height, Math.max(1, mi * MI))) };
}

async function comingOver(planes, where, cache, howMany) {
  // The planes due over where you are, soonest first, with names (routes for the first two).
  const ground = where.groundFt !== null && where.groundFt !== undefined ? where.groundFt : 0;
  const coming = crossings(planes, where.lat, where.lon, ground).slice(0, howMany || 6);
  const routes = await Promise.all(coming.slice(0, 2).map((c) => routeFor(c.plane, cache)));
  return coming.map((c, i) => {
    const r = routes[i] || {};
    return { label: c.plane.label, hex: c.plane.hex, airline: r.airline || "", at: Math.round(now + c.eta),
             eta_s: Math.round(c.eta), route: r.origin && r.destination ? r.origin + " → " + r.destination : "",
             direction: compass(c.az), up: Math.round(c.up), alt_ft: c.plane.alt,
             say: sayName(c.plane.callsign, c.plane.label, r.airline, r.city) };
  });
}

async function livePlanes(where, cache) {
  // What's coming over, straight from adsb.lol (at most once a minute from the same spot).
  const last = cache.live;
  if (last && now - last.updated < 60 && miles(last.lat, last.lon, where.lat, where.lon) < 1) return last;
  const got = await fetchPlanes(where, cache);
  if (!got || got.limited) return null;
  remember(cache, got.planes);
  const next = (await comingOver(got.planes, where, cache)).map(({ hex, ...rest }) => rest);
  const nearby = got.planes.filter((p) => miles(where.lat, where.lon, p.lat, p.lon) <= NEARBY_MILES).length;
  // Something staying in your zone (a helicopter hovering): worth a mention, but it isn't "coming over".
  // Very low and still is probably parked with its transponder on: left out.
  const ground = where.groundFt !== null && where.groundFt !== undefined ? where.groundFt : 0;
  const hovering = got.planes
    .map((p) => ({ p, mi: miles(where.lat, where.lon, p.lat, p.lon) }))
    .filter(({ p, mi }) => mi <= SETTINGS.radius && (p.gs === null || p.gs < HOVER_KT) && p.alt !== null && p.alt - ground >= 300)
    .slice(0, 2).map(({ p, mi }) => ({ label: p.label, say: sayName(p.callsign, p.label, "", ""), miles: mi }));
  // The nearest plane in the air (for "where should I look?" when nothing is coming over).
  let nearest = null;
  const hoveringNow = new Set(hovering.map((h) => h.label));
  for (const p of got.planes) {
    if (p.alt === null || p.alt - ground < 300 || hoveringNow.has(p.label)) continue;  // (already mentioned)
    const l = lookAt(where, p, ground);
    if (l.miles <= 25 && (!nearest || l.miles < nearest.miles)) {
      nearest = { label: p.label, say: sayName(p.callsign, p.label, "", ""), miles: l.miles, direction: compass(l.az),
                  up: Math.round(l.up) };
    }
  }
  cache.live = { lat: where.lat, lon: where.lon, updated: now, next, nearby, hovering, nearest };
  return cache.live;
}

async function tellMyMac(where, cache) {
  // When you've moved, tell planespotter on your Mac where you are, so your plane alerts follow you.
  if (!SETTINGS.follow || !SETTINGS.cmd || !where || !where.fresh) return;
  const prev = cache.shared;
  const moved = !prev || miles(prev.lat, prev.lon, where.lat, where.lon) >= 2;
  if (!moved && now - prev.at < 6 * 3600) return;
  try {
    const req = new Request(SETTINGS.cmd);
    req.method = "POST";
    req.body = "location " + where.lat.toFixed(4) + "," + where.lon.toFixed(4);
    req.timeoutInterval = 5;
    const answer = await withTimeout(req.loadString(), 6);
    const ok = answer !== null && (!req.response || req.response.statusCode < 300);  // ntfy busy (429)? next time
    if (ok) cache.shared = { lat: where.lat, lon: where.lon, at: now };
  } catch (e) {}
}

async function gather() {
  const cache = loadCache();
  const where = await myLocation(cache);
  const home = SETTINGS.home;
  const spot = where || { lat: home.lat, lon: home.lon, groundFt: home.groundFt, fresh: false };
  const atHome = miles(spot.lat, spot.lon, home.lat, home.lon) <= HOME_MILES;
  if (atHome && (where === null || where.groundFt === null || where.groundFt === undefined)) spot.groundFt = home.groundFt;

  const telling = tellMyMac(where, cache);
  const naming = atHome ? Promise.resolve(home.name) : placeName(spot, cache);
  let data = null;
  if (atHome && SETTINGS.mac) {
    data = await getJSON(SETTINGS.mac + "/api/next", 2);  // on your home Wi-Fi: ask the Mac directly
    if (data) data.source = "mac";
  }
  if (!data) {
    const [live, status] = await Promise.all([
      livePlanes(spot, cache),
      atHome && SETTINGS.relay ? getJSON(SETTINGS.relay, forSiri ? 4 : 8) : Promise.resolve(null)]);
    const macKnows = status && now - (status.updated || 0) < 3 * 3600 ? status : null;
    if (live) {
      data = { updated: live.updated, next: live.next, nearby: live.nearby, hovering: live.hovering,
               nearest: live.nearest, source: "live" };
      if (macKnows) {
        for (const k of ["last", "regulars", "hour_count", "space", "transit"]) {
          if (macKnows[k] !== undefined) data[k] = macKnows[k];
        }
      }
    } else if (macKnows) {
      data = { ...macKnows, source: "mac" };  // adsb.lol didn't answer: what your Mac last said
    } else if (cache.live && now - cache.live.updated < 3600) {
      data = { updated: cache.live.updated, next: cache.live.next, source: "saved" };  // the last answer
    }
  }
  const name = await naming;
  await telling;
  saveCache(cache);
  if (data) data.home = name;
  return data;
}

// --- what's true right now ---

function upcoming(data, now) {
  return (data.next || []).filter((p) => p.at > now - GRACE);
}

function lastOver(data, now) {
  // The last plane over: from your Mac, or a plane that was due whose time has since passed.
  const gone = (data.next || []).filter((p) => p.at <= now - GRACE);
  const options = [data.last, gone[gone.length - 1]].filter(Boolean);
  return options.length ? options.reduce((a, b) => (b.at > a.at ? b : a)) : null;
}

function nextRegular(data, now) {
  return (data.regulars || []).find((r) => r.at > now - 10 * 60);
}

// --- Siri ---

function feet(n) {
  return String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");  // 12,450
}

function inMinutes(seconds) {
  const m = Math.max(1, Math.round(seconds / 60));
  return m === 1 ? "in about a minute" : "in about " + m + " minutes";
}

function ago(seconds) {
  const m = Math.round(seconds / 60);
  if (m < 1) return "just now";
  if (m < 60) return m === 1 ? "a minute ago" : m + " minutes ago";
  const h = Math.round(m / 60);
  return h === 1 ? "about an hour ago" : "about " + h + " hours ago";
}

function siriSentence(data, now) {
  if (!data) return "I can't get the planes right now. Check your phone has internet, then try again.";
  const age = now - (data.updated || now);
  if (age > 3 * 3600) {
    return "planespotter's last update was " + ago(age) + ". Is it still running on your Mac, and is the Mac awake?";
  }
  const out = [];
  const next = upcoming(data, now);
  if (next.length) {
    const p = next[0];
    const wait = p.at - now;
    const over = " is coming over now" + (typeof p.alt_ft === "number" ? " at " + feet(p.alt_ft) + " feet" : "");
    let line = (p.say || p.label) + (wait <= 30 ? over : " is due " + inMinutes(wait));
    if (WORDS[p.direction]) line += (wait <= 30 ? ", look up to the " : ", look to the ") + WORDS[p.direction];
    out.push(line + ".");
    if (next.length > 1) {
      const q = next[1];
      out.push("Then " + (q.say || q.label) + " " + inMinutes(Math.max(60, q.at - now)) + ".");
    }
  } else {
    out.push("No planes are due over " + (data.home || "your house") + " in the next few minutes.");
    for (const h of (data.hovering || []).slice(0, 1)) {
      const m = Math.max(1, Math.round(h.miles));
      out.push((h.say || h.label) + " is hovering nearby, about " + m + " mile" + (Math.round(h.miles) > 1 ? "s" : "") + " away.");
    }
    const n = data.nearest;
    if (n && WORDS[n.direction] && !(data.hovering || []).some((h) => h.label === n.label)) {
      const m = Math.max(1, Math.round(n.miles));
      out.push("The nearest plane is " + (n.say || n.label) + ", about " + m + " mile" + (m > 1 ? "s" : "") +
        " away: look " + WORDS[n.direction] + ", about " + n.up + "° up.");
    }
    const r = nextRegular(data, now);
    if (r) {
      out.push("Your next regular, " + (r.say || r.label) + ", usually comes over " +
        (r.at > now ? "at about " + clock(r.at) : "about now") + ".");
    }
    const last = lastOver(data, now);
    if (last) out.push("The last one over was " + (last.say || last.label) + ", " + ago(now - last.at) + ".");
    else if (typeof data.nearby === "number" && !data.nearest) {
      out.push(data.nearby === 1 ? "There's 1 plane within 10 miles." : "There are " + data.nearby + " planes within 10 miles.");
    }
  }
  if (data.transit) out.push(data.transit);
  if (!next.length && data.space) out.push(data.space);
  if (age > 15 * 60) out.push("planespotter last updated " + ago(age) + ".");
  return out.join(" ");
}

// --- the widget ---

function text(stack, value, font, color) {
  const t = stack.addText(value);
  t.font = font;
  t.textColor = color || Color.white();
  t.lineLimit = 1;
  t.minimumScaleFactor = 0.6;
  return t;
}

function countdown(stack, at, size) {
  // A clock that ticks by itself between the widget's refreshes (iPhone decides when those happen).
  const d = stack.addDate(new Date(at * 1000));
  d.applyTimerStyle();
  d.font = Font.boldMonospacedSystemFont(size);
  d.textColor = AMBER;
  d.rightAlignText();
  d.lineLimit = 1;
}

function buildWidget(data, now) {
  const w = new ListWidget();
  w.backgroundColor = new Color("#0b0f14");
  w.url = URLScheme.forRunningScript();  // tap: open the live map
  w.setPadding(12, 14, 12, 14);

  const head = w.addStack();
  text(head, "✈ " + (data ? data.home : "planespotter"), Font.boldSystemFont(12), AMBER);
  w.addSpacer(small ? 4 : 6);

  if (!data) {
    w.refreshAfterDate = new Date((now + 5 * 60) * 1000);
    text(w, "Can't get the planes", Font.boldSystemFont(14));
    const t = w.addText("No internet? It'll try again soon.");
    t.font = Font.systemFont(11);
    t.textColor = MUTED;
    return w;
  }

  // 1. Planes about to come over.
  const next = upcoming(data, now);
  const soon = next.length ? Math.max(now + 60, Math.min(now + 5 * 60, next[0].at + 30)) : now + 5 * 60;
  w.refreshAfterDate = new Date(soon * 1000);  // a hint: iPhone decides when widgets really refresh
  for (const p of next.slice(0, small ? 1 : 2)) {
    const row = w.addStack();
    row.centerAlignContent();
    text(row, p.label, Font.boldSystemFont(small ? 16 : 15));
    row.addSpacer();
    if (p.at <= now + 5) text(row, "overhead", Font.boldSystemFont(14), AMBER);
    else countdown(row, p.at, small ? 15 : 14);
    text(w, [p.route || p.airline, "look " + p.direction + ", " + p.up + "° up"].filter(Boolean).join(" · "),
      Font.systemFont(11), MUTED);
    w.addSpacer(3);
  }

  // 2. Otherwise (or as well, on a medium widget): your next regular flight (at home).
  const regular = nextRegular(data, now);
  if (regular && (!next.length || (!small && next.length < 2))) {  // only what fits
    const row = w.addStack();
    row.centerAlignContent();
    text(row, "Next regular  ", Font.systemFont(11), MUTED);
    text(row, regular.label, Font.boldSystemFont(14));
    row.addSpacer();
    if (regular.at > now) countdown(row, regular.at, 13);
    else text(row, "due now", Font.boldSystemFont(12), AMBER);
    text(w, [regular.route, "usually " + clock(regular.at)].filter(Boolean).join(" · "), Font.systemFont(11), MUTED);
    w.addSpacer(3);
  } else if (!next.length) {
    text(w, "Nothing due right now", Font.boldSystemFont(14));
    w.addSpacer(3);
  }

  // 3. The last plane over, and how busy it is.
  const last = lastOver(data, now);
  if (last) {
    const row = w.addStack();
    row.centerAlignContent();
    text(row, "Last over  ", Font.systemFont(11), MUTED);
    text(row, last.label + " · ", Font.semiboldSystemFont(11));
    const d = row.addDate(new Date(last.at * 1000));
    d.applyRelativeStyle();
    d.font = Font.systemFont(11);
    d.textColor = MUTED;
  }
  if (typeof data.hour_count === "number") {
    const n = data.hour_count;
    text(w, n + " plane" + (n === 1 ? "" : "s") + " in the last hour", Font.systemFont(11), MUTED);
  } else if (typeof data.nearby === "number") {
    text(w, data.nearby + " plane" + (data.nearby === 1 ? "" : "s") + " within 10 mi", Font.systemFont(11), MUTED);
  }
  if (!small && !next.length && data.space) text(w, "🛰️ " + data.space, Font.systemFont(10), MUTED);
  if (data.updated && now - data.updated > 20 * 60) {
    text(w, "updated " + Math.round((now - data.updated) / 60) + " min ago", Font.systemFont(9), MUTED);
  }
  return w;
}

// --- track a flight: search by flight number, airline, airport, or a mix ---

function norm(text) {
  return String(text || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, " ").trim();
}

function airlineMatches(words) {
  // Airlines the words name: "SWA", "WN", "Southwest", "south"... best first.
  const text = " " + words.join(" ") + " ";
  const out = [];
  for (const a of AIRLINES) {
    const [icao, iata, name] = a;
    const short = norm(name).replace(/ (airlines|airline|airways|air lines)$/, "");
    let score = 0;
    for (const w of words) {
      const W = w.toUpperCase();
      if (W === icao) score = Math.max(score, 90);
      else if (iata && W === iata) score = Math.max(score, 88);
      else if (w.length >= 4 && short.startsWith(w)) score = Math.max(score, 70);
    }
    if (text.includes(" " + short + " ") || text.includes(" " + norm(name) + " ")) score = Math.max(score, 95);
    if (score) out.push({ a, score });
  }
  return out.sort((x, y) => y.score - x.score);
}

function airportMatches(words, list, airlineWords) {
  // Airports the words name: "LAX", "KLAX", "Los Angeles", "Heathrow"... best first. (Words that named
  // the airline don't count: "Southwest 1672" isn't about Southwest Florida airport.)
  words = words.filter((w) => !(airlineWords || new Set()).has(w));
  const text = " " + words.join(" ") + " ";
  const out = [];
  for (const ap of list) {
    const [iata, icao, name, city] = ap;
    let score = 0;
    for (const w of words) {
      const W = w.toUpperCase();
      if (iata && W === iata) score = Math.max(score, 90);
      else if (icao && W === icao) score = Math.max(score, 92);
    }
    const c = norm(city), n = norm(name).replace(/\b(international|intl|regional|municipal|airport|airfield|field)\b/g, " ").replace(/\s+/g, " ").trim();
    // A whole name of two words or more ("Los Angeles") beats a code hidden in it (LOS is Lagos).
    if (c && text.includes(" " + c + " ")) score = Math.max(score, c.includes(" ") ? 93 : 75);
    if (n && text.includes(" " + n + " ")) score = Math.max(score, n.includes(" ") ? 94 : 85);
    else if (n && text.trim().length >= 4 && text.trim().includes(" ") && n.includes(text.trim())) score = Math.max(score, 80);  // "o hare"
    else if (n && text.trim().length >= 5 && n.replace(/ /g, "").includes(text.replace(/ /g, ""))) score = Math.max(score, 78);  // "ohare"
    else if (n) {  // some of its name: the more words that match, the better ("John Wayne" is SNA, not Detroit's Wayne County)
      const nameWords = n.split(" ");
      const hits = words.filter((w) => w.length >= 4 && nameWords.includes(w)).length;
      if (hits && words.some((w) => w.length >= 5 && nameWords.includes(w))) score = Math.max(score, Math.min(88, 68 + 5 * hits));
    }
    if (score) out.push({ ap, score });
  }
  return out.sort((x, y) => y.score - x.score);
}

function parseCsvLine(line) {
  const out = [];
  let cur = "", quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quoted) {
      if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; } else if (ch === '"') quoted = false; else cur += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") { out.push(cur); cur = ""; } else cur += ch;
  }
  out.push(cur);
  return out;
}

async function allAirports() {
  // Every airport (not just the big ones), downloaded once from planespotter's own files and kept.
  const path = fm.joinPath(fm.documentsDirectory(), "planespotter-airports.csv");
  let text = null;
  try { if (fm.fileExists(path)) text = fm.readString(path); } catch (e) {}
  if (!text && SETTINGS.airportsUrl) {
    try {
      const req = new Request(SETTINGS.airportsUrl);
      req.timeoutInterval = 20;
      text = await withTimeout(req.loadString(), 25);
      if (text && text.includes("code,icao,kind")) fm.writeString(path, text); else text = null;
    } catch (e) { text = null; }
  }
  if (!text) return null;
  const out = [];
  for (const line of text.split("\n")) {
    if (!line || line[0] === "#" || line.startsWith("code,")) continue;
    const f = parseCsvLine(line);  // code,icao,kind,name,city,country,lat,lon,elevation_ft
    if (f.length >= 8) out.push([f[0].length === 3 ? f[0] : "", f[1], f[3], f[4], parseFloat(f[6]), parseFloat(f[7])]);
  }
  return out;
}

function spoken(words) {
  // Siri's dictation: "a s a 1349" -> "asa 1349"; "where is alaska airlines flight 1349" -> "alaska airlines 1349".
  const out = [];
  for (const w of words) {
    if (["where", "is", "the", "flight", "number", "plane", "find", "track", "my", "please"].includes(w)) continue;
    if (w.length === 1 && /[a-z]/.test(w) && out.length && /^[a-z]{1,2}$/.test(out[out.length - 1]) &&
        out[out.length - 1].length < 3) out[out.length - 1] += w;
    else out.push(w);
  }
  return out;
}

function readQuery(q, airports) {
  // What the words ask for: flight numbers (as planes broadcast them), a registration, an airline, an airport.
  const words = spoken(norm(q).split(" ").filter(Boolean));
  const airlines = airlineMatches(words);
  const airlineWords = new Set();
  if (airlines.length && airlines[0].score >= 70) {
    const [icao, iata, name] = airlines[0].a;
    const nameWords = norm(name).split(" ");
    for (const w of words) {
      if (w.toUpperCase() === icao || w.toUpperCase() === iata || nameWords.includes(w) ||
          (w.length >= 4 && nameWords[0].startsWith(w))) airlineWords.add(w);
    }
  }
  const ports = airportMatches(words, airports, airlineWords);
  const callsigns = [];
  for (const w of words) {  // "ua1234", "ual1234", "wn12" written together
    const m = w.toUpperCase().match(/^([A-Z]{3}|[A-Z]{2}|[A-Z][0-9]|[0-9][A-Z])0*([0-9]{1,4}[A-Z]?)$/);
    if (!m) continue;
    if (m[1].length === 3 && AIRLINES.some((a) => a[0] === m[1])) callsigns.push(m[1] + m[2]);
    else {
      const a = AIRLINES.find((x) => x[1] === m[1]);
      if (a) callsigns.push(a[0] + m[2]);
    }
  }
  const number = words.find((w) => /^[0-9]{1,4}[a-z]?$/.test(w));
  if (number && airlines.length) {  // "southwest 1234", "ua 1234"
    for (const x of airlines.slice(0, 2)) callsigns.push(x.a[0] + number.toUpperCase().replace(/^0+(?=.)/, ""));
  }
  const raw = q.toUpperCase().split(/\s+/);
  const reg = raw.find((w) => /^N[0-9][0-9A-Z]{0,4}$/.test(w) || /^[A-Z0-9]{1,2}-[A-Z0-9]{3,5}$/.test(w)) || "";
  const airline = airlines.length && airlines[0].score >= 70 ? airlines[0].a : null;
  // A 3-letter word can be both (DAL is Delta, and Dallas Love Field): a flight number wins, then the stronger.
  let airport = ports.length && ports[0].score >= 72 ? ports[0].ap : null;
  if (airport && airline && airlines[0].score > ports[0].score && words.length === 1) airport = null;
  return { words, callsigns: [...new Set(callsigns)], reg, airline, airport,
           unsure: !airport && words.some((w) => w.length >= 3 && !/^[0-9]/.test(w)) && !airline && !callsigns.length };
}

function resultOf(p, where, score, why) {
  const l = where ? lookAt(where, p, 0) : { miles: null };
  return { hex: p.hex, label: p.label, cs: p.callsign, reg: p.reg, type: p.type, lat: p.lat, lon: p.lon, alt: p.alt,
           gs: p.gs, track: p.track, miles: l.miles === null ? null : Math.round(l.miles), score, why };
}

async function search(q, where, cache) {
  // The planes the search asks for, best match first (up to 100; the map shows 10 at a time).
  let ask = readQuery(q, AIRPORTS);
  if (ask.unsure) {  // maybe a smaller airport, or a town: look through every airport
    const all = await allAirports();
    if (all) ask = readQuery(q, all);
  }
  const found = new Map(), notFlying = [];
  const add = (r) => { const old = found.get(r.hex); if (!old || old.score < r.score) found.set(r.hex, r); };
  for (const cs of ask.callsigns.slice(0, 3)) {  // exact flight numbers, anywhere in the world
    const got = await adsbGet("/v2/callsign/" + cs, cache, 10);
    if (got && got.limited) return { limited: true, query: q };
    if (got && got.planes.length) got.planes.forEach((p) => add(resultOf(p, where, 100, "Flight " + cs)));
    else if (got) notFlying.push(cs);
  }
  if (ask.reg) {
    const got = await adsbGet("/v2/reg/" + encodeURIComponent(ask.reg), cache, 10);
    if (got && got.planes && got.planes.length) got.planes.forEach((p) => add(resultOf(p, where, 98, "Registration " + ask.reg)));
    else if (got && !got.limited) notFlying.push(ask.reg);
  }
  let understood = ask.callsigns.length ? "Flight " + ask.callsigns.join(" or ") : ask.reg ? "Registration " + ask.reg : "";
  if (ask.airport || (ask.airline && !ask.callsigns.length)) {
    const centre = ask.airport ? { lat: ask.airport[4], lon: ask.airport[5] } : where;
    const code = ask.airport ? ask.airport[0] || ask.airport[1] : "";
    const nm = ask.airport ? 60 : 200;
    const got = await adsbGet("/v2/point/" + centre.lat.toFixed(4) + "/" + centre.lon.toFixed(4) + "/" + nm, cache, 15);
    if (got && got.limited) return { limited: true, query: q };
    for (const p of (got && got.planes) || []) {
      if (ask.airline && !p.callsign.startsWith(ask.airline[0])) continue;
      const d = miles(centre.lat, centre.lon, p.lat, p.lon);
      // Near an airport: close and low is probably landing there or just took off.
      const score = ask.airport ? 80 - d * 0.5 - (p.alt || 0) / 1500 : 70 - d / 4;
      add(resultOf(p, where, score, ask.airport ? (ask.airline ? ask.airline[2] + " near " + code : "Near " + code)
                                                : ask.airline[2] + " near you"));
    }
    understood = understood || (ask.airline ? ask.airline[2] : "Planes") + (ask.airport ? " near " + code + " (" + ask.airport[3] + ")" : " near you");
  }
  let results = [...found.values()].sort((a, b) => b.score - a.score);
  remember(cache, results.map((r) => ({ callsign: r.cs, hex: r.hex })));
  if (ask.airport) {  // flights really going to or from that airport go first
    const code = ask.airport[0];
    const top = results.slice(0, 20);
    const routes = await Promise.all(top.map((r) => routeFor({ callsign: r.cs, lat: r.lat, lon: r.lon }, cache)));
    top.forEach((r, i) => {
      const rt = routes[i] || {};
      r.airline = rt.airline || "";
      if (rt.origin && rt.destination) r.route = rt.origin + " → " + rt.destination;
      if (code && (rt.destination === code || rt.origin === code)) {
        r.score += 40;
        r.why = (rt.destination === code ? "Flying to " : "Flying from ") + code;
      }
    });
    results.sort((a, b) => b.score - a.score);
  }
  if (!understood) understood = "Couldn't tell what that is: try a flight number (UA 1234), an airline or an airport";
  return { query: q, understood, results: results.slice(0, 100), notFlying, canFollow: !!SETTINGS.cmd };
}

async function sendCommand(text) {
  // To planespotter on your Mac (or server), through your private command topic.
  if (!SETTINGS.cmd) return false;
  try {
    const req = new Request(SETTINGS.cmd);
    req.method = "POST";
    req.body = text;
    req.timeoutInterval = 8;
    const answer = await withTimeout(req.loadString(), 9);
    return answer !== null && (!req.response || req.response.statusCode < 300);
  } catch (e) {
    return false;
  }
}

// --- gate numbers (optional): your own key for FlightAware AeroAPI or Aviationstack ---
// Looked up only when you ask (🚪 Gate, or "Where's my plane?"), kept for two hours, and never more
// than SETTINGS.gates.monthly a month (just under the service's free allowance).

const isoTime = (text) => (text ? Date.parse(text) / 1000 : NaN);

function fromAeroApi(body) {
  // The flight that matters now: in the air, else one that arrived in the last 12 hours, else the next one.
  const flights = (body && body.flights) || [];
  const done = (f) => isoTime(f.actual_in || f.actual_on);
  let f = flights.find((x) => (x.actual_off && !x.actual_on) || (x.actual_out && !x.actual_in && !x.actual_on));
  if (!f) {
    const arrived = flights.filter((x) => !isNaN(done(x))).sort((a, b) => done(b) - done(a));
    const upcoming = flights.filter((x) => !x.actual_out && !isNaN(isoTime(x.scheduled_out)))
      .sort((a, b) => isoTime(a.scheduled_out) - isoTime(b.scheduled_out));
    f = (arrived[0] && now - done(arrived[0]) < 12 * 3600 && arrived[0]) || upcoming[0] || arrived[0] || flights[0];
  }
  if (!f) return null;
  const o = f.origin || {}, d = f.destination || {};
  const t = (k) => { const v = isoTime(f[k]); return isNaN(v) ? null : v; };
  const late = (secs, est, sch) => (typeof secs === "number" ? Math.round(secs / 60) : est && sch ? Math.round((est - sch) / 60) : null);
  const phase = f.cancelled ? "cancelled" : t("actual_in") || t("actual_on") ? "landed" : t("actual_off") ? "air"
    : t("actual_out") ? "taxi" : "scheduled";
  const lp = f.last_position;  // where FlightAware last had it (not in every answer)
  const pos = lp && typeof lp.latitude === "number" ? { lat: lp.latitude, lon: lp.longitude, at: isoTime(lp.timestamp) || null } : null;
  return { dep: { code: o.code_iata || o.code || "", city: o.city || "", gate: f.gate_origin || "", terminal: f.terminal_origin || "",
                  scheduled: t("scheduled_out"), estimated: t("estimated_out"), actual: t("actual_out"),
                  delay: late(f.departure_delay, t("estimated_out"), t("scheduled_out")) },
           takeoff: { scheduled: t("scheduled_off"), estimated: t("estimated_off"), actual: t("actual_off") },
           landing: { scheduled: t("scheduled_on"), estimated: t("estimated_on"), actual: t("actual_on") },
           arr: { code: d.code_iata || d.code || "", city: d.city || "", gate: f.gate_destination || "",
                  terminal: f.terminal_destination || "", baggage: f.baggage_claim || "",
                  scheduled: t("scheduled_in"), estimated: t("estimated_in"), actual: t("actual_in"),
                  delay: late(f.arrival_delay, t("estimated_in"), t("scheduled_in")) },
           status: f.status || "", phase, pos, progress: typeof f.progress_percent === "number" ? f.progress_percent : null };
}

function fromAviationstack(body) {
  const data = (body && body.data) || [];
  const d = new Date(now * 1000);
  const day = d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2);
  const f = data.find((x) => x.flight_status === "active") || data.find((x) => x.flight_date === day) || data[0];
  if (!f) return null;
  const dep = f.departure || {}, arr = f.arrival || {};
  const t = (text) => { const v = isoTime(text); return isNaN(v) ? null : v; };
  const st = f.flight_status || "";
  const lv = f.live;  // where Aviationstack last had it (not for every flight)
  const pos = lv && typeof lv.latitude === "number" && !lv.is_ground ? { lat: lv.latitude, lon: lv.longitude, at: isoTime(lv.updated) || null } : null;
  const phase = st === "cancelled" ? "cancelled" : st === "landed" || st === "diverted" ? "landed"
    : st === "active" || st === "incident" ? "air" : t(dep.actual) ? "taxi" : "scheduled";
  return { dep: { code: dep.iata || "", city: dep.airport || "", gate: dep.gate || "", terminal: dep.terminal || "",
                  scheduled: t(dep.scheduled), estimated: t(dep.estimated), actual: t(dep.actual),
                  delay: typeof dep.delay === "number" ? dep.delay : null },
           takeoff: { scheduled: null, estimated: t(dep.estimated_runway), actual: t(dep.actual_runway) },
           landing: { scheduled: null, estimated: t(arr.estimated_runway), actual: t(arr.actual_runway) },
           arr: { code: arr.iata || "", city: arr.airport || "", gate: arr.gate || "", terminal: arr.terminal || "",
                  baggage: arr.baggage || "", scheduled: t(arr.scheduled), estimated: t(arr.estimated), actual: t(arr.actual),
                  delay: typeof arr.delay === "number" ? arr.delay : null },
           status: st, phase, pos, progress: null };
}

async function gateLookup(cs, cache, opts) {
  // opts.force: ask again now; opts.anyAge: whatever we last heard (the map uses that, to save lookups).
  const g = SETTINGS.gates;
  if (!g || !cs) return { off: true };
  cache.gates = cache.gates || {};
  const hit = cache.gates[cs];
  if (hit && !(opts && opts.force) && !gateStale(hit, opts)) return { ...hit.info, asOf: hit.at };  // asked lately: free
  const d = new Date(now * 1000), month = d.getFullYear() + "-" + (d.getMonth() + 1);
  if (!cache.gateUsage || cache.gateUsage.month !== month) cache.gateUsage = { month, n: 0 };
  if (cache.gateUsage.n >= (g.monthly || 90)) return { error: "limit" };
  cache.gateUsage.n += 1;
  let status = 0, body = null;
  try {
    const req = new Request(g.api === "aeroapi"
      ? "https://aeroapi.flightaware.com/aeroapi/flights/" + encodeURIComponent(cs)
      : "https://api.aviationstack.com/v1/flights?access_key=" + encodeURIComponent(g.key) + "&flight_icao=" + encodeURIComponent(cs));
    req.timeoutInterval = 12;
    if (g.api === "aeroapi") req.headers = { "x-apikey": g.key };
    const text = await withTimeout(req.loadString(), 14);
    status = req.response ? req.response.statusCode : 0;
    body = text ? JSON.parse(text) : null;
  } catch (e) {
    body = null;
  }
  const err = body && body.error, code = err && typeof err === "object" ? String(err.code) : "";
  let info;
  if (!body) info = { error: "other" };
  else if (status === 401 || status === 403 ||
           ["invalid_access_key", "missing_access_key", "inactive_user", "101", "https_access_restricted", "105"].includes(code)) info = { error: "key" };
  else if (status === 429 || ["usage_limit_reached", "104", "rate_limit_reached", "106"].includes(code)) info = { error: "limit" };
  else if (status >= 400 || err) info = { error: "other" };
  else info = (g.api === "aeroapi" ? fromAeroApi(body) : fromAviationstack(body)) || { none: true };
  if (!info.error) {
    info.asOf = now;
    cache.gates[cs] = { at: now, info };
    for (const [k, v] of Object.entries(cache.gates)) if (now - v.at > 2 * 86400) delete cache.gates[k];
  }
  return info;
}

function gateStale(hit, opts) {
  // Is a saved answer probably out of date? Schedules don't change much until around take-off; then the
  // flight moves on (in the air, landed), so ask again. (opts.anyAge: the map, which saves lookups.)
  const age = now - hit.at, info = hit.info;
  if (!info || info.none) return age > 30 * 60;
  if (info.phase === "landed" || info.phase === "cancelled") return age > 2 * 3600;
  if (info.phase === "air") return age > 30 * 60;
  const off = takeoffAt(info);
  if (off && now > off - 20 * 60) return age > 15 * 60;  // around (or past) take-off: it may have left
  return opts && opts.anyAge ? false : age > 30 * 60;
}

function serviceName() {
  return SETTINGS.gates ? SETTINGS.gates.name.replace(" AeroAPI", "") : "the flight-status service";
}

function gateWords(info, side) {  // "gate 23B, terminal 2"
  const s = info && info[side];
  return s && s.gate ? "gate " + s.gate + (s.terminal ? ", terminal " + s.terminal : "") : "";
}

async function gateSentence(mine, cache, phase) {
  // For "Where's my plane?": phase is "air", "ground" or "landed".
  if (!SETTINGS.gates || !mine.cs) return phase === "air" ? "" : " No known gate.";
  const info = await gateLookup(mine.cs, cache);
  if (info.error) {
    if (phase === "air") return "";
    return info.error === "limit" ? " No known gate (this month's gate lookups are used up)." : " No known gate.";
  }
  if (phase === "air") {
    const at = gateWords(info, "arr");
    return at ? " It's due at " + at + "." : "";
  }
  const arrived = phase === "landed" || /arriv|landed|taxi(ing)? in/i.test(info.status || "");
  const w = gateWords(info, arrived ? "arr" : "dep");
  if (!w) return " No known gate.";
  return (arrived ? " It's at " : " It leaves from ") + w + "." +
    (arrived && info.arr.baggage ? " Bags come out at carousel " + info.arr.baggage + "." : "");
}

// --- "Hey Siri, where's my plane?" ---

function nearestAirport(lat, lon, list) {
  let best = null, bestMi = 1e9;
  for (const ap of list || []) {
    if (typeof ap[4] !== "number" || isNaN(ap[4])) continue;
    const d = miles(lat, lon, ap[4], ap[5]);
    if (d < bestMi) { bestMi = d; best = ap; }
  }
  return best ? { ap: best, miles: bestMi } : null;
}

function whereIs(lat, lon) {
  // "about 80 miles north-east of Las Vegas" (from the nearest big airport's town)
  const n = nearestAirport(lat, lon, AIRPORTS);
  if (!n) return "";
  const town = n.ap[3] || n.ap[2];
  if (n.miles < 15) return "near " + town;
  const m = n.miles < 100 ? Math.round(n.miles / 5) * 5 : Math.round(n.miles / 10) * 10;
  return "about " + feet(m) + " miles " + WORDS[compass(bearing(n.ap[4], n.ap[5], lat, lon))] + " of " + town;
}

const STATES = { AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California", CO: "Colorado",
  CT: "Connecticut", DE: "Delaware", FL: "Florida", GA: "Georgia", HI: "Hawaii", ID: "Idaho", IL: "Illinois", IN: "Indiana",
  IA: "Iowa", KS: "Kansas", KY: "Kentucky", LA: "Louisiana", ME: "Maine", MD: "Maryland", MA: "Massachusetts",
  MI: "Michigan", MN: "Minnesota", MS: "Mississippi", MO: "Missouri", MT: "Montana", NE: "Nebraska", NV: "Nevada",
  NH: "New Hampshire", NJ: "New Jersey", NM: "New Mexico", NY: "New York", NC: "North Carolina", ND: "North Dakota",
  OH: "Ohio", OK: "Oklahoma", OR: "Oregon", PA: "Pennsylvania", RI: "Rhode Island", SC: "South Carolina",
  SD: "South Dakota", TN: "Tennessee", TX: "Texas", UT: "Utah", VT: "Vermont", VA: "Virginia", WA: "Washington",
  WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming", DC: "Washington, D.C." };

async function regionOf(lat, lon) {
  // "over Montana", "over the Pacific Ocean" (Apple's map lookup), or "" if it doesn't know.
  try {
    const a = ((await withTimeout(Location.reverseGeocode(lat, lon), 3)) || [])[0];
    if (!a) return "";
    if (a.ocean) return "over the " + String(a.ocean).replace(/^the /i, "");
    if (a.inlandWater) return "over " + a.inlandWater;
    const area = a.administrativeArea || "";
    const name = a.isoCountryCode === "US" && STATES[area] ? STATES[area] : area;
    return name ? "over " + name : a.country ? "over " + a.country : "";
  } catch (e) {
    return "";
  }
}

async function whereAbout(lat, lon) {
  // Near a big town: "about 20 miles north-east of Long Beach". Out in the open: "over Montana, about
  // 320 miles east of Spokane".
  const near = whereIs(lat, lon);
  const n = nearestAirport(lat, lon, AIRPORTS);
  if (n && n.miles < 60) return near;
  const region = await regionOf(lat, lon);
  return region ? region + (near ? ", " + near : "") : near;
}

function agoLong(seconds) {
  const m = Math.round(seconds / 60);
  if (m < 2) return "just now";
  if (m < 60) return "about " + m + " minutes ago";
  const h = Math.round(seconds / 3600);
  if (h < 36) return h === 1 ? "about an hour ago" : "about " + h + " hours ago";
  const d = Math.round(seconds / 86400);
  return d === 1 ? "about a day ago" : "about " + d + " days ago";
}

function inTime(seconds) {  // "about 1 hour 20 minutes"
  const m = Math.round(seconds / 60);
  if (m < 60) return "about " + Math.max(1, m) + " minute" + (m === 1 ? "" : "s");
  const h = Math.floor(m / 60), r = Math.round((m % 60) / 10) * 10;
  return "about " + h + " hour" + (h > 1 ? "s" : "") + (r && r < 60 ? " " + r + " minutes" : "");
}

async function airportByCode(code) {
  if (!code) return null;
  const match = (a) => a[0] === code || a[1] === code;
  return AIRPORTS.find(match) || ((await allAirports()) || []).find(match) || null;
}

async function landedAt(lat, lon) {
  // The airport a plane landed at: a big one within 15 miles, else any airport within 10.
  const big = nearestAirport(lat, lon, AIRPORTS);
  if (big && big.miles <= 15) return big.ap;
  const any = nearestAirport(lat, lon, await allAirports());
  return any && any.miles <= 10 ? any.ap : big && big.miles <= 40 ? big.ap : null;
}

function counted(cache) {
  // One more adsb.lol request today, if we still have room (shared with everything else).
  const day = today();
  if (!cache.usage || cache.usage.day !== day) cache.usage = { day, n: 0 };
  if (cache.usage.n >= DAILY_LIMIT) return false;
  cache.usage.n += 1;
  return true;
}

async function landingFromTrace(mine, cache) {
  // When and where it landed, from the plane's recorded positions (adsb.lol keeps about a day of them).
  if (!mine.hex || !counted(cache)) return null;
  const data = await getJSON(SETTINGS.adsb + "/1/trace/full/" + mine.hex, 12);
  if (!data || !Array.isArray(data.trace)) return null;
  const base = Number(data.timestamp) || 0;
  const pts = data.trace.filter((p) => typeof p[1] === "number" && typeof p[2] === "number")
    .map((p) => ({ t: base + p[0], lat: p[1], lon: p[2], ground: p[3] === "ground", alt: typeof p[3] === "number" ? p[3] : null }));
  const seen = mine.seen || 0;  // when it was last seen flying as your flight
  let flying = false, landing = null;
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i];
    if (!p.ground && p.alt !== null && p.alt >= 1000) flying = true;
    // Back on the ground (or a long gap after it was low: receivers often miss the very end).
    const gapLow = i > 0 && p.t - pts[i - 1].t > 20 * 60 && pts[i - 1].alt !== null && pts[i - 1].alt < 3000;
    if (flying && (p.ground || gapLow) && pts[i - 1] && pts[i - 1].t >= seen - 60) {
      landing = p.ground ? p : pts[i - 1];
      flying = false;
      if (seen) break;  // the landing after we saw it; without that, the last landing of the day
    }
  }
  if (!landing && flying && pts.length) {  // the record ends low, a while ago: it landed there
    const last = pts[pts.length - 1];
    if (last.t >= seen - 60 && last.alt !== null && last.alt < 3000 && now - last.t > 10 * 60) landing = last;
  }
  if (!landing) {
    const last = [...pts].reverse().find((p) => !p.ground && p.alt !== null);
    if (last && last.t >= seen - 60) mine.lastSeen = { at: last.t, lat: last.lat, lon: last.lon, alt: last.alt };
    return null;
  }
  const ap = await landedAt(landing.lat, landing.lon);
  return { at: landing.t, lat: landing.lat, lon: landing.lon, code: ap ? ap[0] || ap[1] : "", town: ap ? ap[3] || ap[2] : "" };
}

function flightName(mine) {
  const airline = (AIRLINES.find((a) => mine.cs && mine.cs.startsWith(a[0])) || [])[2] || "";
  return sayName(mine.cs || "", mine.label || mine.cs || "your plane", airline, "");
}

function remember(cache, planes) {
  // Which aircraft each flight number was flying (so a flight that's out of sight can still be found in
  // adsb.lol's record of that aircraft).
  cache.hexes = cache.hexes || {};
  for (const p of planes || []) if (p.callsign && p.hex) cache.hexes[p.callsign] = { hex: p.hex, at: now };
  const keys = Object.keys(cache.hexes);
  if (keys.length > 600) {
    keys.sort((a, b) => cache.hexes[a].at - cache.hexes[b].at).slice(0, keys.length - 500).forEach((k) => delete cache.hexes[k]);
  }
  for (const k of keys) if (cache.hexes[k] && now - cache.hexes[k].at > 3 * 86400) delete cache.hexes[k];
}

function townOf(code) {
  const ap = AIRPORTS.find((a) => a[0] === code || a[1] === code);
  return ap ? ap[3] || ap[2] : code;
}

function lateText(min) {
  const m = Math.abs(Math.round(min));
  const t = m < 60 ? m + " minute" + (m === 1 ? "" : "s") : Math.floor(m / 60) + " hour" + (m >= 120 ? "s" : "") +
    (m % 60 ? " " + (m % 60) + " minutes" : "");
  return min > 0 ? t + " late" : t + " early";
}

function takeoffAt(info) {
  // When it should take off: the runway time if known, else about 15 minutes after leaving the gate.
  const k = info.takeoff || {}, d = info.dep || {};
  return k.estimated || k.scheduled || ((d.estimated || d.scheduled) ? (d.estimated || d.scheduled) + 15 * 60 : null);
}

function scheduleSentence(name, info) {
  // From the gate service: not taken off yet, in the air but out of sight, landed, or cancelled.
  const d = info.dep, a = info.arr;
  if (info.phase === "cancelled") return name + " is cancelled.";
  if (info.phase === "scheduled" || info.phase === "taxi") {
    const off = takeoffAt(info);
    let text = name + (info.phase === "taxi" ? " has left the gate" : " hasn't taken off yet") + ". It leaves " +
      (townOf(d.code) || "its airport") + (d.gate ? " from gate " + d.gate + (d.terminal ? ", terminal " + d.terminal : "") : "") +
      (off ? (off > now + 60 ? ", taking off " + inTime(off - now).replace("about ", "in about ") : ", taking off any minute now") : "") + ".";
    if (typeof d.delay === "number" && Math.abs(d.delay) >= 5) text += " It's running " + lateText(d.delay) + ".";
    else if (typeof d.delay === "number") text += " It's on time.";
    return text;
  }
  if (info.phase === "air") {
    // The free receivers can't see it, but the service (FlightAware sees much more) says it's flying.
    const due = (info.landing && (info.landing.estimated || info.landing.scheduled)) || a.estimated || a.scheduled;
    const left = (info.takeoff && info.takeoff.actual) || d.actual;
    let text = serviceName() + " says " + name + " is in the air" +
      (d.code ? ": it took off from " + townOf(d.code) + (left ? " " + agoLong(now - left) : "") : "") +
      (a.code ? (d.code ? " and" : ":") + " is due in " + townOf(a.code) + (due && due > now ? " " + inTime(due - now).replace("about ", "in about ") : "") : "") + ".";
    if (info.pos) {
      text += " Its last reported position" + (info.pos.at ? ", " + agoLong(now - info.pos.at) + "," : "") + " was " +
        (info.pos.text || whereIs(info.pos.lat, info.pos.lon)) + ".";
    } else if (typeof info.progress === "number") {
      text += " It's about " + Math.round(info.progress) + "% of the way there.";
    }
    if (a.gate) text += " It arrives at gate " + a.gate + (a.terminal ? ", terminal " + a.terminal : "") + ".";
    return text + " The free plane receivers can't see it right now.";
  }
  return "";  // landed: said elsewhere
}

async function describeFlight(f, cache, where) {
  // Where a flight is: in the air (and where to look, if it's near you), on the ground, landed, not yet
  // flying, or last seen. f: { cs, label, hex, seen, landed }.
  const name = flightName(f);
  // A flight number flies every day, often on a different aircraft: only trust the aircraft we saw flying
  // it in the last 12 hours.
  const known = f.cs && cache.hexes && cache.hexes[f.cs];
  if (!f.hex && known && now - known.at < 12 * 3600) {
    f.hex = known.hex;
    f.seen = f.seen || known.at;
  }
  // 1. Right now, as this flight (anywhere in the world)?
  {
    // The aircraft we know it flies (it may not be sending its flight number yet on the ground), then the
    // flight number anywhere in the world.
    const ours = (a) => !f.cs || String(a.flight || "").trim() === f.cs || (f.hex && a.hex === f.hex && !String(a.flight || "").trim());
    let body = null, raw = null;
    if (f.hex && counted(cache)) {
      body = await getJSON(SETTINGS.adsb + "/v2/hex/" + f.hex, 8);
      raw = body && Array.isArray(body.ac) ? body.ac.find(ours) : null;
    }
    if (!raw && f.cs && counted(cache)) {
      body = await getJSON(SETTINGS.adsb + "/v2/callsign/" + f.cs, 8);
      raw = body && Array.isArray(body.ac) ? body.ac.find(ours) : null;
    }
    if (raw && raw.hex) { f.hex = raw.hex; remember(cache, [{ callsign: f.cs, hex: raw.hex }]); }
    if (raw && raw.alt_baro === "ground" && typeof raw.lat === "number") {
      const ap = await landedAt(raw.lat, raw.lon);
      return name + " is on the ground" + (ap ? " at " + (ap[3] || ap[2]) : " " + whereIs(raw.lat, raw.lon)) +
        ((raw.gs || 0) > 5 ? ", taxiing" : "") + "." + (await gateSentence(f, cache, "ground"));
    }
    const age = Math.min(300, Math.max(0, body && body.now ? now - body.now / 1000 : 0));
    const p = raw ? parsePlane(raw, age) : null;
    if (p) {
      f.seen = now;
      f.landed = null;
      let text = name + " is in the air";
      if (where) {  // where it is from you
        const l = lookAt(where, p, where.groundFt || 0);
        text += " " + (l.miles < 1.5 ? "right over you" : "about " + feet(Math.round(l.miles)) + " miles " + WORDS[compass(l.az)] + " of you");
        const near = await whereAbout(p.lat, p.lon);
        if (near && l.miles > 15) text += ", " + near;
      } else text += " " + (await whereAbout(p.lat, p.lon));
      if (p.alt) text += ", at " + feet(Math.round(p.alt / 100) * 100) + " feet";
      const r = await routeFor(p, cache);
      const dest = r && r.destination ? await airportByCode(r.destination) : null;
      if (dest) {
        const d = miles(p.lat, p.lon, dest[4], dest[5]);
        const speed = d > 150 ? Math.max(p.gs || 0, 450) : p.gs;  // still climbing? it'll speed up to cruise
        text += ", heading to " + (dest[3] || dest[2]) + (speed > 80 && d > 3 ? ", " + inTime(d / (speed * 1.15078) * 3600) + " away" : "");
      }
      if (p.vr !== null && p.vr < -600 && p.alt !== null && p.alt < 12000) text += ". It's coming down to land";
      text += ".";
      if (where) {  // close enough to see: which way to look
        const l = lookAt(where, p, where.groundFt || 0);
        if (l.miles <= 60 && l.up >= 1) text += " Look " + WORDS[compass(l.az)] + ", about " + Math.round(l.up) + "° up.";
      }
      return text + (await gateSentence(f, cache, "air"));
    }
  }
  // 2. Not in sight. A landing counts only if it's this trip: in the last 12 hours (or since you picked
  // this flight), and not before the gate service's latest departure for it.
  let landing = (await landingFromTrace(f, cache)) || f.landed;
  const fresh = (at) => at && at >= (f.since ? f.since - 3600 : now - 12 * 3600);
  if (landing && !fresh(landing.at)) landing = null;
  const info = SETTINGS.gates && f.cs ? await gateLookup(f.cs, cache) : null;
  const known2 = info && !info.error && !info.none && !info.off ? info : null;
  const departed = known2 && (known2.dep.actual || (known2.takeoff && known2.takeoff.actual) || known2.dep.scheduled);
  if (landing && known2 && known2.phase !== "landed" && departed && departed > landing.at) landing = null;  // an earlier trip
  if (known2 && known2.phase !== "landed" && !landing) {  // 3. what the gate service knows
    if (known2.pos) known2.pos.text = await whereAbout(known2.pos.lat, known2.pos.lon);
    let text = scheduleSentence(name, known2);
    if (known2.phase === "air" && !known2.pos && f.lastSeen && fresh(f.lastSeen.at)) {
      text += " The receivers last saw it " + agoLong(now - f.lastSeen.at) + ", " + (await whereAbout(f.lastSeen.lat, f.lastSeen.lon)) + ".";
    }
    if (text) return text;
  }
  if (!landing && known2 && known2.phase === "landed") {
    const at = known2.arr.actual || (known2.landing && known2.landing.actual);
    if (fresh(at)) landing = { at, town: townOf(known2.arr.code) };
  }
  if (landing) {
    f.landed = landing;
    let town = landing.town;
    if (!town && typeof landing.lat === "number") {
      const ap = await landedAt(landing.lat, landing.lon);
      town = ap ? ap[3] || ap[2] : "";
    }
    return name + " has landed" + (town ? " in " + town : "") + ", " + agoLong(now - landing.at) + "." +
      (await gateSentence(f, cache, "landed"));
  }
  if (f.lastSeen && fresh(f.lastSeen.at)) {
    const place = await whereAbout(f.lastSeen.lat, f.lastSeen.lon);
    return name + " isn't showing right now. It was last seen " + agoLong(now - f.lastSeen.at) + ", " + place +
      (f.lastSeen.alt ? ", at " + feet(Math.round(f.lastSeen.alt / 100) * 100) + " feet" : "") +
      (/^over the .*(Ocean|Sea|Gulf)/.test(place) ? ": out of range of the receivers out there."
                                                   : ": probably out of range of the receivers (over an ocean, say).");
  }
  return "I can't see " + name + " right now. It may not have taken off yet, it may be out of range of the receivers " +
    "(over an ocean, say), or it may have landed somewhere I can't tell." +
    (SETTINGS.gates ? "" : " Set up gate numbers in planespotter to hear its schedule too.");
}

async function wheresMyPlane(cache, where) {
  // The flight you picked in Track a flight (or one planespotter on your Mac is following).
  let mine = cache.mine;
  if (!mine && SETTINGS.relay) {
    const status = await getJSON(SETTINGS.relay, 6);
    const f = status && status.myflight;
    if (f) {
      mine = { hex: f.hex || "", label: f.label, cs: f.callsign, since: f.took_off, seen: f.last_seen };
      if (f.landed) mine.landed = { at: f.landed, lat: f.last_lat, lon: f.last_lon, town: f.destination_city || "" };
    }
  }
  if (!mine) {
    return "I don't know which plane is yours yet. Open the planespotter map, tap Track a flight, search for your " +
      "flight, and tap it.";
  }
  return describeFlight(mine, cache, where);
}

async function flightAnswer(q, cache, where) {
  // "Hey Siri, find a flight" ... "Alaska 1349": where that flight is.
  let ask = readQuery(q, AIRPORTS);
  if (!ask.callsigns.length && !ask.reg) {
    return "I didn't catch a flight number. Try something like Alaska 1349, or A S A 1349.";
  }
  for (const cs of ask.callsigns.slice(0, 2)) {  // "DL 12": the first reading that's flying or known
    const f = { cs, label: cs };
    const said = await describeFlight(f, cache, where);
    if (!said.startsWith("I can't see") || cs === ask.callsigns.slice(0, 2).slice(-1)[0]) return said;
  }
  return describeFlight({ cs: ask.reg, label: ask.reg }, cache, where);
}

// --- the live map (tap the widget, or run the script in Scriptable) ---

const MAP_PAGE = `<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover">
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css">
<style>
  html, body { margin: 0; height: 100%; background: #0b0f14; color: #e8eef5;
    font: 15px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; -webkit-text-size-adjust: 100%; }
  button { font: inherit; color: inherit; border: 0; cursor: pointer; }
  .hidden { display: none !important; }
  #map { position: absolute; top: 0; right: 0; bottom: 0; left: 0; }
  #top { position: absolute; top: calc(10px + env(safe-area-inset-top)); left: 10px; right: 10px;
    display: flex; justify-content: space-between; gap: 8px; pointer-events: none; }
  .chip { background: rgba(11, 15, 20, .85); border-radius: 999px; padding: 6px 12px; font-size: 13px;
    color: #9aa7b6; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    -webkit-backdrop-filter: blur(8px); backdrop-filter: blur(8px); }
  .chip b { color: #e8eef5; font-weight: 600; }
  #tools { position: absolute; top: calc(48px + env(safe-area-inset-top)); left: 10px; display: flex; gap: 8px; }
  .tool { background: rgba(20, 27, 36, .92); border-radius: 999px; padding: 8px 14px; font-size: 14px; font-weight: 600;
    box-shadow: 0 2px 10px rgba(0, 0, 0, .35); }
  #card { position: absolute; top: calc(92px + env(safe-area-inset-top)); left: 10px; right: 10px; background: rgba(11, 15, 20, .94);
    border: 1px solid rgba(61, 220, 132, .45); border-radius: 14px; padding: 10px 12px; }
  #card b { font-size: 16px; } #card .sub { color: #9aa7b6; font-size: 13px; margin-top: 2px; }
  #card .btns { display: flex; gap: 8px; margin-top: 8px; }
  .small { background: #1c2632; border-radius: 10px; padding: 7px 12px; font-size: 13px; font-weight: 600; }
  .go { background: #ffb020; color: #111; }
  #panel { position: absolute; left: 10px; right: 10px; bottom: calc(12px + env(safe-area-inset-bottom));
    background: rgba(11, 15, 20, .9); border-radius: 16px; padding: 10px 14px 10px;
    -webkit-backdrop-filter: blur(10px); backdrop-filter: blur(10px); }
  #panel h2, #sheet h2 { margin: 0 0 4px; font-size: 11px; font-weight: 600; letter-spacing: .07em;
    text-transform: uppercase; color: #9aa7b6; }
  .row { display: flex; align-items: center; gap: 10px; padding: 6px 0; border-top: 1px solid rgba(255, 255, 255, .07);
    width: 100%; background: none; text-align: left; }
  .row:first-child { border-top: 0; }
  .who { flex: 1; min-width: 0; }
  .who b { font-weight: 650; }
  .who span { display: block; font-size: 12px; color: #9aa7b6; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .cd { font-variant-numeric: tabular-nums; font-weight: 700; color: #ffb020; font-size: 17px; }
  .why { font-size: 11px; color: #ffb020; white-space: nowrap; }
  .empty { color: #9aa7b6; font-size: 14px; padding: 4px 0 2px; }
  #sheet { position: absolute; z-index: 30; top: 0; left: 0; right: 0; bottom: 0; background: #0b0f14; padding: calc(12px + env(safe-area-inset-top)) 14px 14px;
    overflow-y: auto; -webkit-overflow-scrolling: touch; }
  #sheet form { display: flex; gap: 8px; margin: 6px 0 10px; }
  #q { flex: 1; min-width: 0; background: #141b24; border: 1px solid #2a3644; border-radius: 12px; color: #e8eef5;
    font: 16px -apple-system, sans-serif; padding: 10px 12px; }
  #sheetTop { display: flex; justify-content: space-between; align-items: center; }
  #sheetTop b { font-size: 18px; }
  #hint, #found { color: #9aa7b6; font-size: 13px; margin: 2px 0 8px; }
  #more { width: 100%; margin-top: 10px; }
  #msg { position: absolute; top: 0; right: 0; bottom: 0; left: 0; display: none; align-items: center;
    justify-content: center; text-align: center; padding: 30px; color: #9aa7b6; }
  .maplibregl-ctrl-top-right { top: calc(44px + env(safe-area-inset-top)); }
  .maplibregl-ctrl-attrib { background: rgba(11, 15, 20, .6) !important; }
  .maplibregl-ctrl-attrib a { color: #9aa7b6 !important; }
  .maplibregl-popup-content { background: #141b24; color: #e8eef5; border-radius: 12px; padding: 10px 12px;
    font-size: 14px; box-shadow: 0 6px 24px rgba(0, 0, 0, .5); }
  .maplibregl-popup-anchor-bottom .maplibregl-popup-tip { border-top-color: #141b24; }
  .maplibregl-popup-anchor-top .maplibregl-popup-tip { border-bottom-color: #141b24; }
  .pop b { font-size: 16px; }
  .pop div { color: #9aa7b6; font-size: 13px; margin-top: 3px; }
  .pop button { margin-top: 8px; }
</style>
</head><body>
<div id="map"></div>
<div id="top"><div class="chip" id="where">✈ planespotter</div><div class="chip" id="status">Finding planes…</div></div>
<div id="tools"><button class="tool" id="trackBtn">✈︎ Track a flight</button><button class="tool" id="pointBtn">🔭 Point</button></div>
<div id="card" class="hidden"></div>
<div id="panel"><h2>Next over you</h2><div id="list"><div class="empty">Looking…</div></div></div>
<div id="sheet" class="hidden">
  <div id="sheetTop"><b>Track a flight</b><button class="small" id="sheetClose">Close</button></div>
  <form id="form"><input id="q" type="search" enterkeyhint="search" autocomplete="off" autocorrect="off" autocapitalize="characters"
    placeholder="UA 1234, Southwest LAX, JFK…"><button class="small go" type="submit">Search</button></form>
  <div id="hint">A flight number, an airline, an airport, or a mix: <b>UA 1234</b> · <b>Southwest 1234</b> · <b>LAX</b> · <b>Delta JFK</b> · <b>N12345</b></div>
  <div id="found"></div><div id="results"></div><button class="small hidden" id="more">Load more</button>
</div>
<div id="msg"></div>
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<script>
var state = { planes: [], at: 0, me: null, radius: 3, next: [], due: {}, status: "", where: "", ground: 0,
              tracked: null, canFollow: false };
var routes = {}, asks = [], popup = null, popupHex = null, ready = false, centred = false, map = null;
var search = { results: [], shown: 0, notFlying: [], busy: false }, trail = [], movedAt = 0, followed = {}, gates = {}, pinFor = "";
var R = 3958.8, EMPTY = { type: "FeatureCollection", features: [] }, NL = String.fromCharCode(10);
var WORDS = { N: "north", NE: "north-east", E: "east", SE: "south-east", S: "south", SW: "south-west", W: "west", NW: "north-west" };
function $(id) { return document.getElementById(id); }
function rad(d) { return d * Math.PI / 180; }
function deg(r) { return r * 180 / Math.PI; }
function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return "&#" + c.charCodeAt(0) + ";"; }); }
function miles(a1, o1, a2, o2) {
  var p1 = rad(a1), p2 = rad(a2), dp = p2 - p1, dl = rad(o2 - o1);
  var a = Math.pow(Math.sin(dp / 2), 2) + Math.cos(p1) * Math.cos(p2) * Math.pow(Math.sin(dl / 2), 2);
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
}
function bearing(a1, o1, a2, o2) {
  var p1 = rad(a1), p2 = rad(a2), dl = rad(o2 - o1);
  return (deg(Math.atan2(Math.sin(dl) * Math.cos(p2), Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl))) + 360) % 360;
}
function compass(d) { return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.floor((d % 360) / 45 + 0.5) % 8]; }
function offset(lat, lon, brg, mi) {
  var d = mi / R, b = rad(brg), p1 = rad(lat), l1 = rad(lon);
  var p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b));
  var l2 = l1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
  return [deg(p2), (deg(l2) + 540) % 360 - 180];
}
function circle(lat, lon, mi) {
  var pts = [];
  for (var i = 0; i <= 64; i++) { var q = offset(lat, lon, i * 360 / 64, mi); pts.push([q[1], q[0]]); }
  return { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [pts] } };
}
function glided(p, t, since) {  // where it is now, if it flew straight on since the update
  if (!p.gs || p.track === null || p.track === undefined) return [p.lat, p.lon];
  return offset(p.lat, p.lon, p.track, p.gs * 1.15078 * Math.min(Math.max(0, t - since), 300) / 3600);
}
function altText(a) { return a === null || a === undefined ? "" : a >= 18000 ? "FL" + Math.round(a / 100) : a.toLocaleString("en-US") + " ft"; }
function clockText(s) { s = Math.max(0, Math.round(s)); return Math.floor(s / 60) + ":" + ("0" + (s % 60)).slice(-2); }
function agoText(s) { return s < 60 ? Math.round(s) + " s ago" : Math.round(s / 60) + " min ago"; }
function ask(a) { asks.push(a); }
function takeAsks() { var a = asks; asks = []; return JSON.stringify(a); }
function wantRoute(p) { if (p.cs && !(p.cs in routes)) { routes[p.cs] = null; ask({ kind: "route", cs: p.cs, lat: p.lat, lon: p.lon }); } }
function setRoute(cs, r) { routes[cs] = r || {}; draw(); renderResults(); return true; }
function askGate(cs) { if (!gates[cs]) { gates[cs] = "asking"; ask({ kind: "gate", cs: cs }); draw(); } }
function setGate(cs, info) { gates[cs] = info || { none: true }; draw(); return true; }
function gateHtml(cs) {
  // Gate numbers cost a lookup from your monthly allowance, so only when you tap.
  if (!state.gatesOn || !cs) return "";
  var g = gates[cs];
  if (!g) return '<button class="small" onclick="askGate(' + "'" + esc(cs) + "'" + ')">🚪 Gate</button>';
  if (g === "asking") return "<div>Looking up the gate…</div>";
  if (g.error) return "<div>" + (g.error === "limit" ? "Gate lookups for this month are used up" : g.error === "key"
    ? "The gate service didn't accept your key" : "Couldn't look up the gate") + "</div>";
  if (g.none) return "<div>No known gate</div>";
  var side = function (s, verb) {
    return s && s.gate ? verb + " gate " + esc(s.gate) + (s.terminal ? " (terminal " + esc(s.terminal) + ")" : "") : "";
  };
  var bits = [side(g.dep, (g.dep.code ? esc(g.dep.code) + ": leaves from" : "Leaves from")),
              side(g.arr, (g.arr.code ? esc(g.arr.code) + ": arrives at" : "Arrives at"))].filter(Boolean);
  if (g.arr && g.arr.baggage) bits.push("Bags: carousel " + esc(g.arr.baggage));
  return bits.length ? bits.map(function (b) { return "<div>🚪 " + b + "</div>"; }).join("") : "<div>No known gate</div>";
}
function sayOf(p) {  // "Southwest flight 1672 from Las Vegas"
  if (String(p.label || "").charAt(0) === "~") return "an unidentified plane";
  var r = (p.cs && routes[p.cs]) || {}, airline = (r.airline || p.airline || "").replace(/ (Air Lines|Airlines|Airways)$/, "");
  var m = /^[A-Z]{3}([0-9].*)$/.exec(p.cs || "");
  var name = airline && m ? airline + " flight " + m[1] : airline ? airline + ", " + p.label : p.label;
  return name + (r.city ? " from " + r.city : "");
}
function planeIcon() {
  var c = document.createElement("canvas"); c.width = c.height = 64;
  var x = c.getContext("2d"); x.fillStyle = "#fff"; x.beginPath();
  x.moveTo(32, 4); x.lineTo(36, 12); x.lineTo(36, 24); x.lineTo(58, 38); x.lineTo(58, 43); x.lineTo(36, 36);
  x.lineTo(35, 50); x.lineTo(43, 56); x.lineTo(43, 60); x.lineTo(32, 57); x.lineTo(21, 60); x.lineTo(21, 56);
  x.lineTo(29, 50); x.lineTo(28, 36); x.lineTo(6, 43); x.lineTo(6, 38); x.lineTo(28, 24); x.lineTo(28, 12);
  x.closePath(); x.fill();
  return x.getImageData(0, 0, 64, 64);
}
function showMsg(text) { var m = $("msg"); m.textContent = text; m.style.display = "flex"; }
function planeByHex(hex) {
  if (state.tracked && state.tracked.hex === hex && state.tracked.lat !== undefined) return state.tracked;
  return state.planes.filter(function (q) { return q.hex === hex; })[0];
}
function popupHtml(p) {
  var r = p.cs ? routes[p.cs] : null, bits = [];
  if (r && (r.airline || r.origin)) bits.push(esc(r.airline || "") + (r.origin ? " · " + esc(r.origin) + " → " + esc(r.destination) : ""));
  else if (p.cs && r === null) bits.push("Looking up its route…");
  var what = [p.type, altText(p.alt), p.gs ? Math.round(p.gs) + " kt" : ""].filter(Boolean).join(" · ");
  var tracking = state.tracked && state.tracked.hex === p.hex;
  return '<div class="pop"><b>' + esc(p.label) + "</b>" + bits.map(function (b) { return "<div>" + b + "</div>"; }).join("") +
    (what ? "<div>" + esc(what) + "</div>" : "") + gateHtml(p.cs) +
    (tracking ? "" : '<button class="small go" onclick="trackIt(' + "'" + esc(p.hex) + "'" + ')">✈︎ Track this flight</button>') + "</div>";
}
function openPopup(hex) {
  var p = planeByHex(hex);
  if (!p) return;
  wantRoute(p);
  if (popup) popup.remove();
  var at = glided(p, Date.now() / 1000, p === state.tracked ? state.tracked.at : state.at);
  popupHex = hex;
  map.easeTo({ center: [at[1], at[0]], duration: 400 });  // bring it into view (it may be at the edge)
  popup = new maplibregl.Popup({ closeButton: false, offset: 16, maxWidth: "260px" })
    .setLngLat([at[1], at[0]]).setHTML(popupHtml(p)).addTo(map);
  popup.on("close", function () { if (popupHex === hex) popupHex = null; });
}

// --- tracking a flight ---
function trackIt(hex) {
  var p = planeByHex(hex) || search.results.filter(function (r) { return r.hex === hex; })[0];
  if (!p) return;
  if (popup) popup.remove();
  trail = [];
  state.tracked = { hex: p.hex, label: p.label, cs: p.cs, lat: p.lat, lon: p.lon, alt: p.alt, gs: p.gs, track: p.track,
                    type: p.type, at: Date.now() / 1000, pending: true };
  wantRoute(p);
  movedAt = 0;
  $("sheet").classList.add("hidden");
  if (map && ready) map.flyTo({ center: [p.lon, p.lat], zoom: 8, duration: 800 });
  ask({ kind: "track", hex: p.hex, label: p.label, cs: p.cs });
  draw();
}
function stopTracking() { state.tracked = null; trail = []; pinFor = ""; ask({ kind: "untrack" }); draw(); }
function trackWaiting(cs) {
  // A flight that isn't in the air yet: pinned at its airport (or where it is on the ground) until it takes off.
  trail = []; pinFor = "";
  state.tracked = { waiting: true, cs: cs, label: cs, pending: true };
  $("sheet").classList.add("hidden");
  ask({ kind: "track", hex: "", label: cs, cs: cs });
  draw();
}
function refreshTimes(cs) { gates[cs] = "asking"; ask({ kind: "gaterefresh", cs: cs }); draw(); }
function hms(s) {
  s = Math.max(0, Math.round(s));
  var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return (h ? h + ":" + ("0" + m).slice(-2) : m) + ":" + ("0" + x).slice(-2);
}
function clockAt(t) { return new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); }
function waitingCard(t) {
  var i = t.info || {}, now = Date.now() / 1000, lines = [];
  var where = t.ground && t.airport ? "On the ground at " + t.airport.town + " (" + t.airport.code + ")" + (t.ground.gs > 5 ? ", taxiing" : "")
    : t.ground ? "On the ground" + (t.ground.gs > 5 ? ", taxiing" : "")
    : t.airport ? "At " + t.airport.town + " (" + t.airport.code + ")" : t.pending ? "Looking for it…" : "Where it is isn't known yet";
  var phase = { taxi: "Left the gate", air: "In the air (" + esc(i.source || "") + ") · the free receivers can't see it",
                landed: "Landed", cancelled: "Cancelled" }[i.phase];
  lines.push(phase || "Not in the air yet");
  if (i.phase === "air") {
    if (i.from) lines.push("Took off from " + esc(i.from) + (i.left ? " at " + clockAt(i.left) : ""));
    if (i.pos) lines.push("Last reported " + esc(i.pos.town || "") + (i.pos.at ? " (" + clockAt(i.pos.at) + ")" : ""));
    if (i.to) lines.push("Due in " + esc(i.to) + (i.due ? " at about " + clockAt(i.due) + (i.due > now ? " · <span class='cd'>" + hms(i.due - now) + "</span>" : "") : ""));
    if (i.arrGate) lines.push("🚪 Arrives at gate " + esc(i.arrGate));
  } else if (i.phase === "landed") {
    lines.push("In " + esc(i.to || "?") + (i.arrGate ? " · 🚪 gate " + esc(i.arrGate) : ""));
  } else lines.push(where);
  if (i.phase !== "air") {
    if (i.gate) lines.push("🚪 Gate " + i.gate + (i.terminal ? ", terminal " + i.terminal : ""));
    else if (t.info) lines.push("No known gate");
  }
  if (i.takeoff && i.phase !== "air" && i.phase !== "landed" && i.phase !== "cancelled") {
    lines.push(i.takeoff > now + 30 ? "<span class='cd'>Takes off in " + hms(i.takeoff - now) + "</span> (about " + clockAt(i.takeoff) + ")"
                                    : "Should be taking off about now");
  }
  if (typeof i.delay === "number") lines.push(i.delay >= 5 ? "⏱ Delayed " + i.delay + " min" : i.delay <= -5 ? "⏱ " + (-i.delay) + " min early" : "⏱ On time");
  if (i.asOf) lines.push("<span style='font-size:11px'>Times from " + esc(i.source || "") + ", updated " + Math.max(0, Math.round((now - i.asOf) / 60)) + " min ago</span>");
  if (!t.gatesOn) lines.push("Where it leaves from, its gate, take-off time and delays: set up gate numbers (see the README)");
  else if (t.infoError === "limit") lines.push("Gate lookups for this month are used up");
  var f = followed[t.cs];
  var follow = !state.canFollow ? "" : f === "yes" ? "<span class='sub'>🔔 You'll hear when it takes off</span>"
    : '<button class="small" onclick="followIt(' + "'" + esc(t.cs) + "'" + ')">' + (f === "asking" ? "Asking…" : "🔔 Tell me when it flies") + "</button>";
  var times = t.gatesOn ? '<button class="small" onclick="refreshTimes(' + "'" + esc(t.cs) + "'" + ')">' + (gates[t.cs] === "asking" ? "Updating…" : "↻ Times") + "</button>" : "";
  return "<b>✈︎ " + esc(t.label) + "</b>" + lines.map(function (l) { return "<div class='sub'>" + l + "</div>"; }).join("") +
    "<div class='btns'>" + follow + times + "<button class='small' onclick='stopTracking()'>Stop</button></div>";
}
function followIt(flight) { followed[flight] = "asking"; ask({ kind: "follow", flight: flight }); draw(); renderResults(); }
function followDone(r) { followed[r.flight] = r.ok ? "yes" : "no"; draw(); renderResults(); return true; }
function renderCard() {
  var t = state.tracked, card = $("card");
  if (!t) { card.classList.add("hidden"); return; }
  card.classList.remove("hidden");
  if (t.waiting) { card.innerHTML = waitingCard(t); return; }
  if (t.lost) {
    card.innerHTML = "<b>" + esc(t.label) + "</b><div class='sub'>Not showing any more: it has probably landed (or flown out of range).</div>" +
      "<div class='btns'><button class='small' onclick='stopTracking()'>OK</button></div>";
    return;
  }
  var r = (t.cs && routes[t.cs]) || {}, now = Date.now() / 1000, at = glided(t, now, t.at), bits = [];
  if (r.airline || t.airline) bits.push(r.airline || t.airline);
  if (r.origin) bits.push(r.origin + " → " + r.destination + (r.city ? " (from " + r.city + ")" : ""));
  var meta = [t.type, altText(t.alt), t.gs ? Math.round(t.gs) + " kt" : ""];
  if (state.me && t.lat !== undefined) meta.push(Math.round(miles(state.me.lat, state.me.lon, at[0], at[1])) + " mi " +
    compass(bearing(state.me.lat, state.me.lon, at[0], at[1])) + " of you");
  var f = t.cs ? followed[t.cs] : null;
  var follow = !state.canFollow || !t.cs ? "" : f === "yes" ? "<span class='sub'>🔔 Your Mac will tell you when it lands</span>"
    : f === "no" ? "<span class='sub'>Couldn't reach your Mac</span>"
    : '<button class="small" onclick="followIt(' + "'" + esc(t.cs) + "'" + ')">' + (f === "asking" ? "Asking…" : "🔔 Notify me when it lands") + "</button>";
  if (t.gone && t.lat === undefined) {
    card.innerHTML = "<b>✈︎ " + esc(t.label) + "</b><div class='sub'>" + (t.unseen > 120 ? "Not seen for " + Math.round(t.unseen / 60) +
      " min: out of range of receivers (over the ocean, say), or it has landed. Still looking…" : "Looking for it…") + "</div>" +
      "<div class='btns'><button class='small' onclick='stopTracking()'>Stop</button></div>";
    return;
  }
  card.innerHTML = "<b>✈︎ " + esc(t.label) + "</b>" + (t.gone ? " <span class='sub'>(not seen for a moment)</span>" : "") +
    "<div class='sub'>" + esc(bits.join(" · ")) + "</div><div class='sub'>" + esc(meta.filter(Boolean).join(" · ")) + "</div>" +
    "<div class='sub'>" + gateHtml(t.cs) + "</div>" +
    "<div class='btns'>" + follow + "<button class='small' onclick='stopTracking()'>Stop</button></div>";
}

// --- the search sheet ---
function openSearch() { $("sheet").classList.remove("hidden"); setTimeout(function () { $("q").focus(); }, 50); }
function runSearch(e) {
  if (e) e.preventDefault();
  var q = $("q").value.trim();
  if (!q || search.busy) return false;
  search = { results: [], shown: 0, notFlying: [], busy: true, query: q };
  $("found").textContent = "Searching…"; $("results").innerHTML = ""; $("more").classList.add("hidden");
  $("q").blur();
  ask({ kind: "search", q: q });
  return false;
}
function searchDone(res) {
  search.busy = false;
  if (res.limited) { $("found").textContent = "Paused for today (adsb.lol's daily allowance for this phone)."; return true; }
  search.results = res.results || []; search.notFlying = res.notFlying || []; search.shown = Math.min(10, search.results.length);
  state.canFollow = res.canFollow;
  $("found").textContent = res.understood + " · " + (search.results.length ? search.results.length + " found" : "nothing in the air");
  renderResults();
  return true;
}
function renderResults() {
  if ($("sheet").classList.contains("hidden")) return;
  var html = search.notFlying.map(function (cs) {
    var f = followed[cs];
    return '<div class="row"><div class="who"><b>' + esc(cs) + "</b><span>Not in the air right now</span></div>" +
      '<button class="small go" onclick="trackWaiting(' + "'" + esc(cs) + "'" + ')">📍 Track</button> ' +
      (!state.canFollow ? "" : f === "yes" ? '<span class="why">🔔 You’ll hear when it takes off</span>'
        : '<button class="small" onclick="followIt(' + "'" + esc(cs) + "'" + ')">' + (f === "asking" ? "Asking…" : "🔔 Tell me when it flies") + "</button>") + "</div>";
  }).join("");
  html += search.results.slice(0, search.shown).map(function (r, i) {
    wantRoute(r);
    var rt = routes[r.cs] || {}, sub = [rt.airline || r.airline, rt.origin ? rt.origin + " → " + rt.destination : r.route].filter(Boolean).join(" · ");
    var meta = [r.type, altText(r.alt), r.miles !== null ? r.miles + " mi away" : ""].filter(Boolean).join(" · ");
    return '<button class="row" onclick="trackIt(' + "'" + esc(r.hex) + "'" + ')"><div class="who"><b>' + esc(r.label) + "</b><span>" +
      esc(sub || "Route not known") + "</span><span>" + esc(meta) + '</span></div><div class="why">' + esc(r.why) + "</div></button>";
  }).join("");
  $("results").innerHTML = html;
  $("more").classList.toggle("hidden", search.shown >= search.results.length);
}
function loadMore() { search.shown = Math.min(search.results.length, search.shown + 10); renderResults(); }

// --- drawing ---
function draw() {
  var t = Date.now() / 1000;
  var next = (state.next || []).filter(function (p) { return p.at > t - 90; }).slice(0, 3);
  $("list").innerHTML = next.length ? next.map(function (p) {
    var sub = [p.route || p.airline, "look " + p.direction + ", " + p.up + "° up"].filter(Boolean).join(" · ");
    return '<div class="row"><div class="who"><b>' + esc(p.label) + "</b><span>" + esc(sub) + "</span></div>" +
      '<div class="cd">' + (p.at <= t + 5 ? "overhead" : clockText(p.at - t)) + "</div></div>";
  }).join("") : '<div class="empty">Nothing due over you in the next 15 minutes.</div>';
  $("where").innerHTML = "✈ <b>" + esc(state.where || "planespotter") + "</b>";
  $("status").textContent = state.status || (state.at ? state.planes.length + " planes · " + agoText(t - state.at) : "Finding planes…");
  renderCard();
  if (!ready) return;
  var tracked = state.tracked && state.tracked.lat !== undefined && !state.tracked.lost ? state.tracked : null;
  var w = state.tracked && state.tracked.waiting ? state.tracked : null, spot = w && (w.ground || w.airport);
  map.getSource("waiting").setData(spot ? { type: "Feature", geometry: { type: "Point", coordinates: [spot.lon, spot.lat] },
    properties: { text: w.label + (w.info && w.info.gate ? NL + "gate " + w.info.gate : "") } } : EMPTY);
  var features = state.planes.filter(function (p) { return !tracked || p.hex !== tracked.hex; }).map(function (p) {
    var at = glided(p, t, state.at);
    return { type: "Feature", geometry: { type: "Point", coordinates: [at[1], at[0]] },
      properties: { hex: p.hex, track: p.track || 0, alt: p.alt === null ? 99999 : p.alt, due: !!state.due[p.hex],
                    text: p.label + (p.alt !== null ? "\\n" + altText(p.alt) : "") } };
  });
  map.getSource("planes").setData({ type: "FeatureCollection", features: features });
  if (tracked) {
    var at = glided(tracked, t, tracked.at);
    map.getSource("tracked").setData({ type: "Feature", geometry: { type: "Point", coordinates: [at[1], at[0]] },
      properties: { hex: tracked.hex, track: tracked.track || 0, text: tracked.label + "\\n" + altText(tracked.alt) } });
    map.getSource("trail").setData({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: trail.concat([[at[1], at[0]]]) } });
  } else {
    map.getSource("tracked").setData(EMPTY);
    map.getSource("trail").setData(EMPTY);
  }
  if (state.me) {
    map.getSource("zone").setData(circle(state.me.lat, state.me.lon, state.radius));
    map.getSource("me").setData({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [state.me.lon, state.me.lat] } });
  }
  if (popup && popupHex) {
    var p = planeByHex(popupHex);
    if (p) { var pa = glided(p, t, p === state.tracked ? state.tracked.at : state.at); popup.setLngLat([pa[1], pa[0]]).setHTML(popupHtml(p)); }
  }
}
function frame() {
  // The first view: you, your zone, and the next planes due over you.
  var t = Date.now() / 1000, pts = [[state.me.lon, state.me.lat]];
  var z = circle(state.me.lat, state.me.lon, state.radius).geometry.coordinates[0];
  pts.push(z[0], z[16], z[32], z[48]);
  state.planes.forEach(function (p) {
    if (state.due[p.hex]) { var at = glided(p, t, state.at); pts.push([at[1], at[0]]); }
  });
  var b = new maplibregl.LngLatBounds(pts[0], pts[0]);
  pts.forEach(function (q) { b.extend(q); });
  map.fitBounds(b, { padding: { top: 100, bottom: $("panel").offsetHeight + 40, left: 40, right: 40 }, maxZoom: 11, duration: 0 });
}
function update(d) {
  for (var k in d) state[k] = d[k];
  if (d.gateFresh) for (var c in d.gateFresh) gates[c] = d.gateFresh[c];
  var w = state.tracked && state.tracked.waiting ? state.tracked : null, spot = w && (w.ground || w.airport);
  if (spot && map && ready && pinFor !== w.cs + (w.ground ? ":g" : "")) {  // fly to where it's waiting, once
    pinFor = w.cs + (w.ground ? ":g" : "");
    map.flyTo({ center: [spot.lon, spot.lat], zoom: w.ground ? 13 : 11, duration: 900 });
  }
  if (d.gatesKnown) for (var g in d.gatesKnown) if (!gates[g] || gates[g] === "asking") gates[g] = d.gatesKnown[g];
  var t = state.tracked;
  if (t && !t.waiting && !t.lost && t.lat !== undefined && !t.gone) {
    trail.push([t.lon, t.lat]);
    if (map && ready && Date.now() - movedAt > 20000) map.easeTo({ center: [t.lon, t.lat], duration: 800 });  // follow it
  }
  if (!centred && state.me && map && !t) {
    draw();  // the panel first, so the map knows how much room it takes
    if (ready) { frame(); centred = true; }
    else map.jumpTo({ center: [state.me.lon, state.me.lat], zoom: Math.max(7, 10.6 - Math.log2(Math.max(1, state.radius))) });
  }
  draw();
  return true;
}



try {
  map = new maplibregl.Map({ container: "map", style: "https://tiles.openfreemap.org/styles/dark",
    center: [-98, 39], zoom: 3, attributionControl: false, dragRotate: false, pitchWithRotate: false });
  map.addControl(new maplibregl.AttributionControl({ compact: true }), "top-right");
  map.on("dragstart", function () { movedAt = Date.now(); });
  map.on("load", function () {
    map.addImage("plane", planeIcon(), { sdf: true });
    ["zone", "me", "planes", "tracked", "trail", "waiting"].forEach(function (id) { map.addSource(id, { type: "geojson", data: EMPTY }); });
    map.addLayer({ id: "zone-fill", type: "fill", source: "zone", paint: { "fill-color": "#ffb020", "fill-opacity": 0.08 } });
    map.addLayer({ id: "zone-line", type: "line", source: "zone",
      paint: { "line-color": "#ffb020", "line-width": 1.5, "line-opacity": 0.75, "line-dasharray": [2, 2] } });
    map.addLayer({ id: "trail", type: "line", source: "trail", paint: { "line-color": "#3ddc84", "line-width": 2.5, "line-opacity": 0.8 } });
    var label = { "text-field": ["get", "text"], "text-font": ["Noto Sans Regular"], "text-size": 11, "text-offset": [0, 1.5],
      "text-anchor": "top", "text-optional": true };
    map.addLayer({ id: "planes", type: "symbol", source: "planes",
      layout: Object.assign({ "icon-image": "plane", "icon-size": ["case", ["get", "due"], 0.62, 0.46], "icon-rotate": ["get", "track"],
        "icon-rotation-alignment": "map", "icon-allow-overlap": true, "icon-ignore-placement": true }, label),
      paint: { "icon-color": ["case", ["get", "due"], "#ffb020", ["<", ["get", "alt"], 10000], "#7cc4ff", "#c9d3de"],
        "text-color": "#e8eef5", "text-halo-color": "#0b0f14", "text-halo-width": 1.4 } });
    map.addLayer({ id: "tracked", type: "symbol", source: "tracked",
      layout: Object.assign({ "icon-image": "plane", "icon-size": 0.75, "icon-rotate": ["get", "track"],
        "icon-rotation-alignment": "map", "icon-allow-overlap": true, "icon-ignore-placement": true }, label),
      paint: { "icon-color": "#3ddc84", "text-color": "#3ddc84", "text-halo-color": "#0b0f14", "text-halo-width": 1.4 } });
    map.addLayer({ id: "waiting", type: "circle", source: "waiting",
      paint: { "circle-radius": 9, "circle-color": "#3ddc84", "circle-stroke-width": 3, "circle-stroke-color": "#0b0f14" } });
    map.addLayer({ id: "waiting-label", type: "symbol", source: "waiting",
      layout: { "text-field": ["get", "text"], "text-font": ["Noto Sans Regular"], "text-size": 12, "text-offset": [0, 1.3],
        "text-anchor": "top", "text-allow-overlap": true },
      paint: { "text-color": "#3ddc84", "text-halo-color": "#0b0f14", "text-halo-width": 1.6 } });
    map.addLayer({ id: "me", type: "circle", source: "me",
      paint: { "circle-radius": 7, "circle-color": "#3b9cff", "circle-stroke-width": 2.5, "circle-stroke-color": "#ffffff" } });
    map.on("click", "planes", function (e) { openPopup(e.features[0].properties.hex); });
    map.on("click", "tracked", function (e) { openPopup(e.features[0].properties.hex); });
    var credits = document.querySelector(".maplibregl-ctrl-attrib");
    if (credits) credits.classList.remove("maplibregl-compact-show");  // just the (i) button until tapped
    ready = true;
    if (!centred && state.me && !state.tracked) { frame(); centred = true; }
    draw();
  });
} catch (e) {
  showMsg("The map couldn't start (it needs an internet connection). The list below still works.");
}
$("trackBtn").onclick = openSearch;
$("sheetClose").onclick = function () { $("sheet").classList.add("hidden"); };
$("form").onsubmit = runSearch;
$("more").onclick = loadMore;
$("pointBtn").onclick = function () { ask({ kind: "point" }); };  // opens Point in Safari (it has the compass)
setInterval(draw, 1000);
</script>
</body></html>`;

function pointLink(planes, next, where, name) {
  // Point runs in Safari (Scriptable's own web view doesn't get the compass). adsb.lol doesn't let web pages
  // fetch from it, so the planes go in the link, after "#": that part never leaves your phone.
  const r4 = (x) => Math.round(x * 1e4) / 1e4;
  const d = { v: 1, at: Math.round(now), me: [r4(where.lat), r4(where.lon)], g: Math.round(where.groundFt || 0),
              r: SETTINGS.radius, home: name,
              p: planes.filter((p) => p.alt !== null).slice(0, 90).map((p) => [p.hex, p.label, p.callsign, r4(p.lat), r4(p.lon),
                 p.alt, p.gs === null ? null : Math.round(p.gs), p.track === null ? null : Math.round(p.track), p.type]),
              n: (next || []).map((x) => [x.hex, x.at]),
              back: URLScheme.forRunningScript() + "?point=1" };
  return SETTINGS.pointUrl + "#" + encodeURIComponent(JSON.stringify(d));
}

async function pointNow() {
  // "Point Planespotter" (or ↻ on the Point page): fresh planes around you, then Point in Safari.
  const cache = loadCache();
  const home = SETTINGS.home;
  const where = (await myLocation(cache)) || { lat: home.lat, lon: home.lon, groundFt: home.groundFt, fresh: false };
  const atHome = miles(where.lat, where.lon, home.lat, home.lon) <= HOME_MILES;
  if (atHome && (where.groundFt === null || where.groundFt === undefined)) where.groundFt = home.groundFt;
  const name = atHome ? home.name : await placeName(where, cache);
  const got = await fetchPlanes(where, cache);
  if (!got || !got.planes) {
    saveCache(cache);
    const a = new Alert();
    a.title = "Can't get the planes";
    a.message = got && got.limited ? "This phone has asked adsb.lol as often as it should today: try tomorrow."
      : "Check your phone has internet, then try again.";
    a.addAction("OK");
    await a.present();
    return;
  }
  remember(cache, got.planes);
  const next = await comingOver(got.planes, where, cache, 6);
  saveCache(cache);
  Safari.open(pointLink(got.planes, next, where, name));
}

async function page(wv, code) {
  try {
    return await wv.evaluateJavaScript(code);
  } catch (e) {
    return null;  // closed, or still loading
  }
}

async function showMap() {
  // A live map of the planes around you. Planes move between updates; it asks adsb.lol every 15 s
  // for the first 10 minutes, then every minute (and pauses for the day at DAILY_LIMIT). It also
  // searches for flights and follows the one you pick, anywhere in the world.
  const cache = loadCache();
  const home = SETTINGS.home;
  const wv = new WebView();
  await wv.loadHTML(MAP_PAGE, "https://planespotter.local/");
  let open = true;
  wv.present(true).then(() => { open = false; }, () => { open = false; });
  const started = now;
  let last = -1e12, name = "", told = false, ticks = 0, seenNow = null;
  let where = { lat: home.lat, lon: home.lon, groundFt: home.groundFt, fresh: false };
  while (open) {
    tick();
    if (now - last >= (now - started < 600 ? 15 : 60)) {
      last = now;
      ticks += 1;
      where = (await myLocation(cache)) || where;
      const atHome = miles(where.lat, where.lon, home.lat, home.lon) <= HOME_MILES;
      if (atHome && (where.groundFt === null || where.groundFt === undefined)) where.groundFt = home.groundFt;
      if (!told) { told = true; await tellMyMac(where, cache); }
      if (!name || !atHome) name = atHome ? home.name : await placeName(where, cache);
      const update = { me: { lat: where.lat, lon: where.lon }, radius: SETTINGS.radius, where: name, status: "",
                       ground: where.groundFt || 0, canFollow: !!SETTINGS.cmd, gatesOn: !!SETTINGS.gates };
      if (ticks === 1 && cache.gates) {  // gates looked up in the last two hours: shown without asking again
        update.gatesKnown = {};
        for (const [cs, v] of Object.entries(cache.gates)) if (now - v.at < 2 * 3600) update.gatesKnown[cs] = v.info;
      }
      const following = cache.tracking;
      if (!following || ticks % 4 === 1) {  // the planes around you (less often while following a flight)
        const got = await fetchPlanes(where, cache);
        if (got && got.planes) {
          const next = await comingOver(got.planes, where, cache, 6);
          seenNow = { planes: got.planes, next, where: { ...where }, name };  // for 🔭 Point
          update.at = got.at;
          update.planes = got.planes.map((p) => ({ hex: p.hex, label: p.label, cs: p.callsign, type: p.type,
            lat: p.lat, lon: p.lon, alt: p.alt, gs: p.gs, track: p.track }));
          update.next = next;
          update.due = Object.fromEntries(next.map((p) => [p.hex, true]));
        } else if (got && got.limited) {
          update.status = "Paused for today (adsb.lol's daily allowance)";
        } else {
          update.status = "No connection: trying again…";
        }
      }
      if (following && !following.hex) {  // not in the air yet: watch for it taking off, and show where it waits
        const body = counted(cache) ? await getJSON(SETTINGS.adsb + "/v2/callsign/" + following.cs, 10) : null;
        const raw = body && Array.isArray(body.ac) ? body.ac.find((x) => String(x.flight || "").trim() === following.cs) : null;
        if (raw && raw.alt_baro !== "ground" && typeof raw.lat === "number") {  // it's taken off: follow it live
          following.hex = raw.hex;
          following.seen = now;
          remember(cache, [{ callsign: following.cs, hex: raw.hex }]);
          if (cache.mine && cache.mine.cs === following.cs) { cache.mine.hex = raw.hex; cache.mine.seen = now; }
        } else {
          const info = SETTINGS.gates ? await gateLookup(following.cs, cache, { anyAge: true }) : null;
          const ok = info && !info.error && !info.none && !info.off;
          // Only the gate service says reliably where it leaves from (route databases are often out of date).
          const code = ok && info.phase !== "air" && info.phase !== "landed" ? info.dep.code : "";
          const ap = code ? await airportByCode(code) : null;
          if (ok && info.pos) info.pos.town = await whereAbout(info.pos.lat, info.pos.lon);
          update.tracked = { waiting: true, hex: "", cs: following.cs, label: following.label, gatesOn: !!SETTINGS.gates,
            airport: ap ? { code: ap[0] || ap[1], town: ap[3] || ap[2], lat: ap[4], lon: ap[5] } : null,
            ground: raw && typeof raw.lat === "number" ? { lat: raw.lat, lon: raw.lon, gs: raw.gs || 0 } : null,
            info: ok ? { gate: info.dep.gate, terminal: info.dep.terminal, takeoff: takeoffAt(info), delay: info.dep.delay,
                         status: info.status, phase: info.phase, from: info.dep.code, to: info.arr.code,
                         left: (info.takeoff && info.takeoff.actual) || info.dep.actual,
                         due: (info.landing && (info.landing.estimated || info.landing.scheduled)) || info.arr.estimated || info.arr.scheduled,
                         arrGate: info.arr.gate, pos: info.pos, asOf: info.asOf, source: serviceName() } : null,
            infoError: (info && info.error) || "" };
        }
      }
      if (following && following.hex) {  // the flight you're following, wherever it is
        const got = await adsbGet("/v2/hex/" + following.hex, cache, 10);
        if (got && got.planes && got.planes.length) {
          following.seen = now;
          const p = got.planes[0];
          if (cache.mine && cache.mine.hex === p.hex && (!cache.mine.cs || cache.mine.cs === p.callsign)) cache.mine.seen = now;
          const r = await routeFor(p, cache);
          update.tracked = { ...resultOf(p, where, 0, ""), at: now, airline: r.airline || "" };
        } else if (got && !got.limited) {
          // Not showing: out of range of receivers for a while (over an ocean), or it has landed.
          const unseen = now - (following.seen || following.since || now);
          update.tracked = { hex: following.hex, label: following.label, cs: following.cs, gone: true,
                             unseen: Math.round(unseen) };
          if (unseen > 3 * 3600) {
            cache.tracking = null;
            update.tracked = { hex: following.hex, label: following.label, lost: true };
          }
        }
      } else if (!following) {
        update.tracked = null;
      }
      await page(wv, "update(" + JSON.stringify(update) + ")");
      saveCache(cache);
    }
    for (const a of JSON.parse((await page(wv, "takeAsks()")) || "[]")) {
      if (a.kind === "route") {
        const r = await routeFor({ callsign: a.cs, lat: a.lat, lon: a.lon }, cache);
        await page(wv, "setRoute(" + JSON.stringify(a.cs) + "," + JSON.stringify(r || {}) + ")");
      } else if (a.kind === "search") {
        await page(wv, "searchDone(" + JSON.stringify(await search(a.q, where, cache)) + ")");
      } else if (a.kind === "track") {
        cache.tracking = { hex: a.hex || "", label: a.label, cs: a.cs, since: now, seen: a.hex ? now : null };
        cache.mine = { hex: a.hex || "", label: a.label, cs: a.cs, since: now, seen: a.hex ? now : null };  // "Where's my plane?"
        last = -1e12;  // show it now
      } else if (a.kind === "untrack") {
        cache.tracking = null;
        last = -1e12;
      } else if (a.kind === "point") {  // 🔭 Point: in Safari, with the planes we just got
        if (seenNow) Safari.open(pointLink(seenNow.planes, seenNow.next, seenNow.where, seenNow.name));
        else await pointNow();
      } else if (a.kind === "gaterefresh") {  // ↻ Times: ask the gate service again (one lookup)
        const info = await gateLookup(a.cs, cache, { force: true });
        await page(wv, "setGate(" + JSON.stringify(a.cs) + "," + JSON.stringify(info) + ")");
        last = -1e12;
      } else if (a.kind === "gate") {
        await page(wv, "setGate(" + JSON.stringify(a.cs) + "," + JSON.stringify(await gateLookup(a.cs, cache)) + ")");
      } else if (a.kind === "follow") {
        // planespotter on your Mac follows it: tells you when it takes off, flies over home, and lands
        const ok = await sendCommand("flight " + a.flight);
        await page(wv, "followDone(" + JSON.stringify({ flight: a.flight, ok }) + ")");
      }
      saveCache(cache);
    }
    await sleep(1);
  }
  saveCache(cache);
}

function siriAsked() {
  // What the Siri shortcut passed in: "my plane", or what you said when it asked "Which flight?".
  if (typeof args === "undefined") return "";
  const bits = [args.shortcutParameter, ...(args.plainTexts || [])].flat()
    .filter((x) => typeof x === "string" && x.trim());
  return bits.join(" ").trim();
}

if (config.runsInApp) {
  // Tapped the widget, or ran it in Scriptable: the live map. ("point" from a shortcut: straight to pointing.)
  const asked = JSON.stringify((typeof args !== "undefined" && (args.queryParameters || args.plainTexts)) || "");
  if (/point/i.test(asked)) await pointNow();  // "Point Planespotter", or ↻ on the Point page
  else await showMap();
} else if (forSiri && siriAsked()) {
  // "Hey Siri, where's my plane?", or "Hey Siri, find a flight" ... "Alaska 1349"
  const cache = loadCache();
  const where = await myLocation(cache);
  const asked = siriAsked();
  Script.setShortcutOutput(/\bmy (plane|flight)\b/i.test(asked) ? await wheresMyPlane(cache, where) : await flightAnswer(asked, cache, where));
  saveCache(cache);
} else {
  const data = await gather();
  if (forSiri) {
    Script.setShortcutOutput(siriSentence(data, now));  // the shortcut's Speak Text says this
  } else {
    Script.setWidget(buildWidget(data, now));
  }
}
Script.complete();

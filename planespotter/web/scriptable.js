// planespotter for the free Scriptable app (https://scriptable.app): your home-screen widget, and Siri.
// It finds the planes coming over wherever your phone is, straight from adsb.lol, so it works on its own:
// at home, at a hotel, on mobile data, with your Mac on or off. Run from a Siri shortcut ("Run Script"),
// it works out what to say at the moment you ask. At home it also shows what planespotter on your Mac
// knows (your regular flights, the last plane over), and when you're away it tells your Mac where you
// are, so your plane alerts follow you. Tap the widget (or run it here) for a live map of the planes
// around you.

const SETTINGS = __SETTINGS__;

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
              alt: alt === null ? null : Math.trunc(alt), gs: typeof raw.gs === "number" ? raw.gs : null, track };
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

async function fetchPlanes(where, cache) {
  // Every plane within RANGE_NM, moved on to now. null if adsb.lol didn't answer; {limited} if we've
  // asked as often as we should today.
  const day = today();
  if (!cache.usage || cache.usage.day !== day) cache.usage = { day, n: 0 };
  if (cache.usage.n >= DAILY_LIMIT) return { limited: true };
  cache.usage.n += 1;
  const url = SETTINGS.adsb + "/v2/point/" + where.lat.toFixed(4) + "/" + where.lon.toFixed(4) + "/" + RANGE_NM;
  const body = await getJSON(url, forSiri ? 5 : 10);
  if (!body || !Array.isArray(body.ac)) return null;
  const age = Math.min(300, Math.max(0, body.now ? now - body.now / 1000 : 0));
  return { planes: body.ac.map((raw) => parsePlane(raw, age)).filter(Boolean), at: now };
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
  const next = (await comingOver(got.planes, where, cache)).map(({ hex, ...rest }) => rest);
  const nearby = got.planes.filter((p) => miles(where.lat, where.lon, p.lat, p.lon) <= NEARBY_MILES).length;
  // Something staying in your zone (a helicopter hovering): worth a mention, but it isn't "coming over".
  // Very low and still is probably parked with its transponder on: left out.
  const ground = where.groundFt !== null && where.groundFt !== undefined ? where.groundFt : 0;
  const hovering = got.planes
    .map((p) => ({ p, mi: miles(where.lat, where.lon, p.lat, p.lon) }))
    .filter(({ p, mi }) => mi <= SETTINGS.radius && (p.gs === null || p.gs < HOVER_KT) && p.alt !== null && p.alt - ground >= 300)
    .slice(0, 2).map(({ p, mi }) => ({ label: p.label, say: sayName(p.callsign, p.label, "", ""), miles: mi }));
  cache.live = { lat: where.lat, lon: where.lon, updated: now, next, nearby, hovering };
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
      data = { updated: live.updated, next: live.next, nearby: live.nearby, hovering: live.hovering, source: "live" };
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
    const r = nextRegular(data, now);
    if (r) {
      out.push("Your next regular, " + (r.say || r.label) + ", usually comes over " +
        (r.at > now ? "at about " + clock(r.at) : "about now") + ".");
    }
    const last = lastOver(data, now);
    if (last) out.push("The last one over was " + (last.say || last.label) + ", " + ago(now - last.at) + ".");
    else if (typeof data.nearby === "number") {
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

// --- the live map (tap the widget, or run the script in Scriptable) ---

const MAP_PAGE = `<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover">
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css">
<style>
  html, body { margin: 0; height: 100%; background: #0b0f14; color: #e8eef5;
    font: 15px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; -webkit-text-size-adjust: 100%; }
  #map { position: absolute; top: 0; right: 0; bottom: 0; left: 0; }
  #top { position: absolute; top: calc(10px + env(safe-area-inset-top)); left: 10px; right: 10px;
    display: flex; justify-content: space-between; gap: 8px; pointer-events: none; }
  .chip { background: rgba(11, 15, 20, .85); border-radius: 999px; padding: 6px 12px; font-size: 13px;
    color: #9aa7b6; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    -webkit-backdrop-filter: blur(8px); backdrop-filter: blur(8px); }
  .chip b { color: #e8eef5; font-weight: 600; }
  #panel { position: absolute; left: 10px; right: 10px; bottom: calc(12px + env(safe-area-inset-bottom));
    background: rgba(11, 15, 20, .9); border-radius: 16px; padding: 10px 14px 10px;
    -webkit-backdrop-filter: blur(10px); backdrop-filter: blur(10px); }
  #panel h2 { margin: 0 0 4px; font-size: 11px; font-weight: 600; letter-spacing: .07em;
    text-transform: uppercase; color: #9aa7b6; }
  .row { display: flex; align-items: center; gap: 10px; padding: 6px 0; border-top: 1px solid rgba(255, 255, 255, .07); }
  .row:first-child { border-top: 0; }
  .who { flex: 1; min-width: 0; }
  .who b { font-weight: 650; }
  .who span { display: block; font-size: 12px; color: #9aa7b6; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .cd { font-variant-numeric: tabular-nums; font-weight: 700; color: #ffb020; font-size: 17px; }
  .empty { color: #9aa7b6; font-size: 14px; padding: 4px 0 2px; }
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
</style>
</head><body>
<div id="map"></div>
<div id="top"><div class="chip" id="where">✈ planespotter</div><div class="chip" id="status">Finding planes…</div></div>
<div id="panel"><h2>Next over you</h2><div id="list"><div class="empty">Looking…</div></div></div>
<div id="msg"></div>
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<script>
var state = { planes: [], at: 0, me: null, radius: 3, next: [], due: {}, status: "", where: "" };
var routes = {}, wanted = [], popup = null, popupHex = null, ready = false, centred = false, map = null;
var R = 3958.8, EMPTY = { type: "FeatureCollection", features: [] };
function rad(d) { return d * Math.PI / 180; }
function deg(r) { return r * 180 / Math.PI; }
function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return "&#" + c.charCodeAt(0) + ";"; }); }
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
function glided(p, t) {  // where it is now, if it flew straight on since the update
  if (!p.gs || p.track === null) return [p.lat, p.lon];
  return offset(p.lat, p.lon, p.track, p.gs * 1.15078 * Math.min(Math.max(0, t - state.at), 300) / 3600);
}
function altText(a) { return a === null ? "" : a >= 18000 ? "FL" + Math.round(a / 100) : a.toLocaleString() + " ft"; }
function clockText(s) { s = Math.max(0, Math.round(s)); return Math.floor(s / 60) + ":" + ("0" + (s % 60)).slice(-2); }
function agoText(s) { return s < 60 ? Math.round(s) + " s ago" : Math.round(s / 60) + " min ago"; }
function planeIcon() {
  var c = document.createElement("canvas"); c.width = c.height = 64;
  var x = c.getContext("2d"); x.fillStyle = "#fff"; x.beginPath();
  x.moveTo(32, 4); x.lineTo(36, 12); x.lineTo(36, 24); x.lineTo(58, 38); x.lineTo(58, 43); x.lineTo(36, 36);
  x.lineTo(35, 50); x.lineTo(43, 56); x.lineTo(43, 60); x.lineTo(32, 57); x.lineTo(21, 60); x.lineTo(21, 56);
  x.lineTo(29, 50); x.lineTo(28, 36); x.lineTo(6, 43); x.lineTo(6, 38); x.lineTo(28, 24); x.lineTo(28, 12);
  x.closePath(); x.fill();
  return x.getImageData(0, 0, 64, 64);
}
function showMsg(text) { var m = document.getElementById("msg"); m.textContent = text; m.style.display = "flex"; }
function popupHtml(p) {
  var r = p.cs ? routes[p.cs] : null, bits = [];
  if (r && (r.airline || r.origin)) bits.push(esc(r.airline || "") + (r.origin ? " · " + esc(r.origin) + " → " + esc(r.destination) : ""));
  else if (p.cs && !(p.cs in routes)) bits.push("Looking up its route…");
  var what = [p.type, altText(p.alt), p.gs ? Math.round(p.gs) + " kt" : ""].filter(Boolean).join(" · ");
  return '<div class="pop"><b>' + esc(p.label) + "</b>" + bits.map(function (b) { return "<div>" + b + "</div>"; }).join("") +
    (what ? "<div>" + esc(what) + "</div>" : "") + "</div>";
}
function openPopup(hex) {
  var p = state.planes.filter(function (q) { return q.hex === hex; })[0];
  if (!p) return;
  if (p.cs && !(p.cs in routes)) wanted.push({ cs: p.cs, lat: p.lat, lon: p.lon });
  if (popup) popup.remove();
  var at = glided(p, Date.now() / 1000);
  popupHex = hex;
  map.easeTo({ center: [at[1], at[0]], duration: 400 });  // bring it into view (it may be at the edge)
  popup = new maplibregl.Popup({ closeButton: false, offset: 16, maxWidth: "260px" })
    .setLngLat([at[1], at[0]]).setHTML(popupHtml(p)).addTo(map);
  popup.on("close", function () { if (popupHex === hex) popupHex = null; });
}
function draw() {
  var t = Date.now() / 1000;
  var next = (state.next || []).filter(function (p) { return p.at > t - 90; }).slice(0, 3);
  document.getElementById("list").innerHTML = next.length ? next.map(function (p) {
    var sub = [p.route || p.airline, "look " + p.direction + ", " + p.up + "° up"].filter(Boolean).join(" · ");
    return '<div class="row"><div class="who"><b>' + esc(p.label) + "</b><span>" + esc(sub) + "</span></div>" +
      '<div class="cd">' + (p.at <= t + 5 ? "overhead" : clockText(p.at - t)) + "</div></div>";
  }).join("") : '<div class="empty">Nothing due over you in the next 15 minutes.</div>';
  document.getElementById("where").innerHTML = "✈ <b>" + esc(state.where || "planespotter") + "</b>";
  document.getElementById("status").textContent = state.status ||
    (state.at ? state.planes.length + " planes · " + agoText(t - state.at) : "Finding planes…");
  if (!ready) return;
  var features = state.planes.map(function (p) {
    var at = glided(p, t);
    return { type: "Feature", geometry: { type: "Point", coordinates: [at[1], at[0]] },
      properties: { hex: p.hex, track: p.track || 0, alt: p.alt === null ? 99999 : p.alt, due: !!state.due[p.hex],
                    text: p.label + (p.alt !== null ? "\\n" + altText(p.alt) : "") } };
  });
  map.getSource("planes").setData({ type: "FeatureCollection", features: features });
  if (state.me) {
    map.getSource("zone").setData(circle(state.me.lat, state.me.lon, state.radius));
    map.getSource("me").setData({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [state.me.lon, state.me.lat] } });
  }
  if (popup && popupHex) {
    var p = state.planes.filter(function (q) { return q.hex === popupHex; })[0];
    if (p) { var at = glided(p, t); popup.setLngLat([at[1], at[0]]).setHTML(popupHtml(p)); }
  }
}
function frame() {
  // The first view: you, your zone, and the next planes due over you.
  var t = Date.now() / 1000, pts = [[state.me.lon, state.me.lat]];
  var z = circle(state.me.lat, state.me.lon, state.radius).geometry.coordinates[0];
  pts.push(z[0], z[16], z[32], z[48]);
  state.planes.forEach(function (p) {
    if (state.due[p.hex]) { var at = glided(p, t); pts.push([at[1], at[0]]); }
  });
  var b = new maplibregl.LngLatBounds(pts[0], pts[0]);
  pts.forEach(function (q) { b.extend(q); });
  var panel = document.getElementById("panel").offsetHeight;
  map.fitBounds(b, { padding: { top: 70, bottom: panel + 40, left: 40, right: 40 }, maxZoom: 11, duration: 0 });
}
function update(d) {
  for (var k in d) state[k] = d[k];
  if (!centred && state.me && map) {
    draw();  // the panel first, so the map knows how much room it takes
    if (ready) { frame(); centred = true; }
    else map.jumpTo({ center: [state.me.lon, state.me.lat], zoom: Math.max(7, 10.6 - Math.log2(Math.max(1, state.radius))) });
  }
  draw();
  return true;
}
function takeWanted() { var w = wanted; wanted = []; return JSON.stringify(w); }
function setRoute(cs, r) { routes[cs] = r || {}; draw(); return true; }
try {
  map = new maplibregl.Map({ container: "map", style: "https://tiles.openfreemap.org/styles/dark",
    center: [-98, 39], zoom: 3, attributionControl: false, dragRotate: false, pitchWithRotate: false });
  map.addControl(new maplibregl.AttributionControl({ compact: true }), "top-right");
  map.on("load", function () {
    map.addImage("plane", planeIcon(), { sdf: true });
    ["zone", "me", "planes"].forEach(function (id) { map.addSource(id, { type: "geojson", data: EMPTY }); });
    map.addLayer({ id: "zone-fill", type: "fill", source: "zone", paint: { "fill-color": "#ffb020", "fill-opacity": 0.08 } });
    map.addLayer({ id: "zone-line", type: "line", source: "zone",
      paint: { "line-color": "#ffb020", "line-width": 1.5, "line-opacity": 0.75, "line-dasharray": [2, 2] } });
    map.addLayer({ id: "planes", type: "symbol", source: "planes",
      layout: { "icon-image": "plane", "icon-size": ["case", ["get", "due"], 0.62, 0.46], "icon-rotate": ["get", "track"],
        "icon-rotation-alignment": "map", "icon-allow-overlap": true, "icon-ignore-placement": true,
        "text-field": ["get", "text"], "text-font": ["Noto Sans Regular"], "text-size": 11, "text-offset": [0, 1.5],
        "text-anchor": "top", "text-optional": true },
      paint: { "icon-color": ["case", ["get", "due"], "#ffb020", ["<", ["get", "alt"], 10000], "#7cc4ff", "#c9d3de"],
        "text-color": "#e8eef5", "text-halo-color": "#0b0f14", "text-halo-width": 1.4 } });
    map.addLayer({ id: "me", type: "circle", source: "me",
      paint: { "circle-radius": 7, "circle-color": "#3b9cff", "circle-stroke-width": 2.5, "circle-stroke-color": "#ffffff" } });
    map.on("click", "planes", function (e) { openPopup(e.features[0].properties.hex); });
    var credits = document.querySelector(".maplibregl-ctrl-attrib");
    if (credits) credits.classList.remove("maplibregl-compact-show");  // just the (i) button until tapped
    ready = true;
    if (!centred && state.me) { frame(); centred = true; }
    draw();
  });
} catch (e) {
  showMsg("The map couldn't start (it needs an internet connection). The list below still works.");
}
setInterval(draw, 1000);
</script>
</body></html>`;

async function page(wv, code) {
  try {
    return await wv.evaluateJavaScript(code);
  } catch (e) {
    return null;  // closed, or still loading
  }
}

async function showMap() {
  // A live map of the planes around you. Planes move between updates; it asks adsb.lol every 15 s
  // for the first 10 minutes, then every minute (and pauses for the day at DAILY_LIMIT).
  const cache = loadCache();
  const home = SETTINGS.home;
  const wv = new WebView();
  await wv.loadHTML(MAP_PAGE, "https://planespotter.local/");
  let open = true;
  wv.present(true).then(() => { open = false; }, () => { open = false; });
  const started = now;
  let last = -1e12, name = "", told = false;
  while (open) {
    tick();
    if (now - last >= (now - started < 600 ? 15 : 60)) {
      last = now;
      const where = (await myLocation(cache)) || { lat: home.lat, lon: home.lon, groundFt: home.groundFt, fresh: false };
      const atHome = miles(where.lat, where.lon, home.lat, home.lon) <= HOME_MILES;
      if (atHome && (where.groundFt === null || where.groundFt === undefined)) where.groundFt = home.groundFt;
      if (!told) { told = true; await tellMyMac(where, cache); }
      if (!name || !atHome) name = atHome ? home.name : await placeName(where, cache);
      const got = await fetchPlanes(where, cache);
      const update = { me: { lat: where.lat, lon: where.lon }, radius: SETTINGS.radius, where: name, status: "" };
      if (got && got.planes) {
        const next = await comingOver(got.planes, where, cache, 6);
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
      await page(wv, "update(" + JSON.stringify(update) + ")");
      saveCache(cache);
    }
    const asked = await page(wv, "takeWanted()");
    for (const w of JSON.parse(asked || "[]")) {
      const r = await routeFor({ callsign: w.cs, lat: w.lat, lon: w.lon }, cache);
      await page(wv, "setRoute(" + JSON.stringify(w.cs) + "," + JSON.stringify(r || {}) + ")");
    }
    await sleep(1);
  }
  saveCache(cache);
}

if (config.runsInApp) {
  await showMap();  // tapped the widget, or ran it in Scriptable
} else {
  const data = await gather();
  if (forSiri) {
    Script.setShortcutOutput(siriSentence(data, now));  // the shortcut's Speak Text says this
  } else {
    Script.setWidget(buildWidget(data, now));
  }
}
Script.complete();

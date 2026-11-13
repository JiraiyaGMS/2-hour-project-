// planespotter for the free Scriptable app (https://scriptable.app): your home-screen widget, and Siri.
// It finds the planes coming over wherever your phone is, straight from adsb.lol, so it works on its own:
// at home, at a hotel, on mobile data, with your Mac on or off. Run from a Siri shortcut ("Run Script"),
// it works out what to say at the moment you ask. At home it also shows what planespotter on your Mac
// knows (your regular flights, the last plane over), and when you're away it tells your Mac where you
// are, so your plane alerts follow you.

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
const WORDS = { N: "north", NE: "north-east", E: "east", SE: "south-east", S: "south", SW: "south-west",
                W: "west", NW: "north-west" };
const now = Date.now() / 1000;

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
      eta = 0;
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

async function livePlanes(where, cache) {
  // Straight from adsb.lol (at most once a minute from the same spot).
  const last = cache.live;
  if (last && now - last.updated < 60 && miles(last.lat, last.lon, where.lat, where.lon) < 1) return last;
  const url = SETTINGS.adsb + "/v2/point/" + where.lat.toFixed(4) + "/" + where.lon.toFixed(4) + "/" + RANGE_NM;
  const body = await getJSON(url, forSiri ? 5 : 10);
  if (!body || !Array.isArray(body.ac)) return null;
  const age = Math.min(300, Math.max(0, body.now ? now - body.now / 1000 : 0));
  const planes = body.ac.map((raw) => parsePlane(raw, age)).filter(Boolean);
  const ground = where.groundFt !== null && where.groundFt !== undefined ? where.groundFt : 0;
  const coming = crossings(planes, where.lat, where.lon, ground).slice(0, 6);
  const routes = await Promise.all(coming.slice(0, 2).map((c) => routeFor(c.plane, cache)));
  const next = coming.map((c, i) => {
    const r = routes[i] || {};
    return { label: c.plane.label, airline: r.airline || "", at: Math.round(now + c.eta), eta_s: Math.round(c.eta),
             route: r.origin && r.destination ? r.origin + " → " + r.destination : "",
             direction: compass(c.az), up: Math.round(c.up), alt_ft: c.plane.alt,
             say: sayName(c.plane.callsign, c.plane.label, r.airline, r.city) };
  });
  const nearby = planes.filter((p) => miles(where.lat, where.lon, p.lat, p.lon) <= NEARBY_MILES).length;
  cache.live = { lat: where.lat, lon: where.lon, updated: now, next, nearby };
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
      data = { updated: live.updated, next: live.next, nearby: live.nearby, source: "live" };
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
    let line = (p.say || p.label) + (wait <= 30 ? " is coming over now" : " is due " + inMinutes(wait));
    if (WORDS[p.direction]) line += (wait <= 30 ? ", look up to the " : ", look to the ") + WORDS[p.direction];
    out.push(line + ".");
    if (next.length > 1) {
      const q = next[1];
      out.push("Then " + (q.say || q.label) + " " + inMinutes(Math.max(60, q.at - now)) + ".");
    }
  } else {
    out.push("No planes are due over " + (data.home || "your house") + " in the next few minutes.");
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
  if (SETTINGS.mac) w.url = SETTINGS.mac;
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

const data = await gather();
if (forSiri) {
  Script.setShortcutOutput(siriSentence(data, now));  // the shortcut's Speak Text says this
} else if (config.runsInWidget) {
  Script.setWidget(buildWidget(data, now));
} else {
  console.log("Siri would say: " + siriSentence(data, now));
  await buildWidget(data, now).presentMedium();
}
Script.complete();

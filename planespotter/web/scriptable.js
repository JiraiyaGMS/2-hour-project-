// planespotter for the free Scriptable app (https://scriptable.app): your home-screen widget, and Siri.
// The widget shows the planes due over your house next, your next regular flight, and the last one over.
// Run from a Siri shortcut ("Run Script"), it works out what to say at the moment you ask.
// It reads planespotter's latest status from your private ntfy topic, so it works anywhere
// (home Wi-Fi, a hotel, mobile data) while planespotter is running on your Mac.

const RELAY = "__RELAY_URL__";               // your status, via ntfy (works anywhere)
const PLANESPOTTER = "__PLANESPOTTER_URL__"; // your Mac (home Wi-Fi only), opened when you tap the widget

const AMBER = new Color("#ffb020");
const MUTED = new Color("#9aa7b6");
const small = config.widgetFamily === "small";
const forSiri = !config.runsInWidget && !config.runsInApp;  // run by a shortcut (Siri)
const GRACE = 90;  // a plane still counts as "coming over now" this long after its time
const WORDS = { N: "north", NE: "north-east", E: "east", SE: "south-east", S: "south", SW: "south-west",
                W: "west", NW: "north-west" };

async function get(url, timeout) {
  try {
    const req = new Request(url);
    req.timeoutInterval = timeout;
    const text = await req.loadString();
    const lines = text.trim().split("\n");
    return JSON.parse(lines[lines.length - 1]);
  } catch (e) {
    return null;
  }
}

async function load() {
  // Straight from the Mac when we can (home Wi-Fi), otherwise the copy on ntfy.
  const direct = PLANESPOTTER.startsWith("http") ? await get(PLANESPOTTER + "/api/next", forSiri ? 2 : 3) : null;
  if (direct) return direct;
  return RELAY.startsWith("http") ? await get(RELAY, forSiri ? 8 : 10) : null;
}

function clock(at) {
  const d = new Date(at * 1000);
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

// --- what's true right now (the status can be a few minutes old) ---

function upcoming(data, now) {
  return (data.next || []).filter((p) => p.at > now - GRACE);
}

function lastOver(data, now) {
  // The last plane over: from the status, or a plane it said was due whose time has since passed.
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
  if (!data) return "I can't reach planespotter right now. Is it running on your Mac, and is the Mac awake?";
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
  if (PLANESPOTTER.startsWith("http")) w.url = PLANESPOTTER;
  w.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000);
  w.setPadding(12, 14, 12, 14);

  const head = w.addStack();
  text(head, "✈ " + (data ? data.home : "planespotter"), Font.boldSystemFont(12), AMBER);
  w.addSpacer(small ? 4 : 6);

  if (!data) {
    text(w, "Can't reach planespotter", Font.boldSystemFont(14));
    const t = w.addText("Is it running on your Mac?");
    t.font = Font.systemFont(11);
    t.textColor = MUTED;
    return w;
  }

  // 1. Planes about to come over.
  const next = upcoming(data, now);
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

  // 2. Otherwise (or as well, on a medium widget): your next regular flight.
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

  // 3. The last plane over, and how busy it's been.
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
  }
  if (!small && !next.length && data.space) text(w, "🛰️ " + data.space, Font.systemFont(10), MUTED);
  if (data.updated && now - data.updated > 20 * 60) {
    text(w, "updated " + Math.round((now - data.updated) / 60) + " min ago", Font.systemFont(9), MUTED);
  }
  return w;
}

const data = await load();
const now = Date.now() / 1000;
if (forSiri) {
  Script.setShortcutOutput(siriSentence(data, now));  // the shortcut's Speak Text says this
} else if (config.runsInWidget) {
  Script.setWidget(buildWidget(data, now));
} else {
  console.log("Siri would say: " + siriSentence(data, now));
  await buildWidget(data, now).presentMedium();
}
Script.complete();

// planespotter home-screen widget, for the free Scriptable app (https://scriptable.app).
// Shows the planes due over your house next, your next regular flight, and the last one over.
// It reads planespotter's latest status from your private ntfy topic, so it works anywhere
// (home Wi-Fi, a hotel, mobile data) while planespotter is running on your Mac.

const RELAY = "__RELAY_URL__";               // your status, via ntfy (works anywhere)
const PLANESPOTTER = "__PLANESPOTTER_URL__"; // your Mac (home Wi-Fi only), opened when you tap the widget

const AMBER = new Color("#ffb020");
const MUTED = new Color("#9aa7b6");
const small = config.widgetFamily === "small";

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
  const direct = PLANESPOTTER.startsWith("http") ? await get(PLANESPOTTER + "/api/next", 3) : null;
  if (direct) return direct;
  return RELAY.startsWith("http") ? await get(RELAY, 10) : null;
}

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

function clock(at) {
  const d = new Date(at * 1000);
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

const data = await load();
const now = Date.now() / 1000;
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
} else {
  // 1. Planes about to come over (the status can be a few minutes old: drop ones already gone).
  const next = (data.next || []).filter((p) => p.at > now - 60);
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
  const regular = (data.regulars || []).find((r) => r.at > now - 10 * 60);
  if (regular && (!next.length || (!small && next.length < 2))) {  // only what fits
    const row = w.addStack();
    row.centerAlignContent();
    text(row, "Next regular  ", Font.systemFont(11), MUTED);
    text(row, regular.label, Font.boldSystemFont(small ? 14 : 14));
    row.addSpacer();
    if (regular.at > now) countdown(row, regular.at, small ? 13 : 13);
    else text(row, "due now", Font.boldSystemFont(12), AMBER);
    text(w, [regular.route, "usually " + clock(regular.at)].filter(Boolean).join(" · "), Font.systemFont(11), MUTED);
    w.addSpacer(3);
  } else if (!next.length) {
    text(w, "Nothing due right now", Font.boldSystemFont(14));
    w.addSpacer(3);
  }

  // 3. The last plane over, and how busy it's been.
  if (data.last) {
    const row = w.addStack();
    row.centerAlignContent();
    text(row, "Last over  ", Font.systemFont(11), MUTED);
    text(row, data.last.label + " · ", Font.semiboldSystemFont(11));
    const d = row.addDate(new Date(data.last.at * 1000));
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
}

if (config.runsInWidget) {
  Script.setWidget(w);
} else {
  await w.presentMedium();
}
Script.complete();

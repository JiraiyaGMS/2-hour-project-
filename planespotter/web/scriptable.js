// planespotter home-screen widget, for the free Scriptable app (https://scriptable.app).
// Shows the next planes due over your house, with a live countdown.
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
  t.minimumScaleFactor = 0.7;
  return t;
}

const data = await load();
const now = Date.now() / 1000;
const w = new ListWidget();
w.backgroundColor = new Color("#0b0f14");
if (PLANESPOTTER.startsWith("http")) w.url = PLANESPOTTER;
w.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000);
w.setPadding(12, 14, 12, 14);

const head = w.addStack();
text(head, "✈ ", Font.boldSystemFont(12), AMBER);
text(head, data ? data.home : "planespotter", Font.boldSystemFont(12), AMBER);
w.addSpacer(6);

// Planes still to come (the status may be a few minutes old, so drop ones already gone).
const next = data ? (data.next || []).filter((p) => p.at > now - 60) : [];

if (!data) {
  text(w, "Can't reach planespotter", Font.boldSystemFont(14));
  const t = w.addText("Is it running on your Mac?");
  t.font = Font.systemFont(11);
  t.textColor = MUTED;
} else if (!next.length) {
  text(w, "Nothing due", Font.boldSystemFont(16));
  text(w, "right now", Font.systemFont(12), MUTED);
  if (data.last) {
    w.addSpacer(4);
    const row = w.addStack();
    text(row, "Last: " + data.last.label + " · ", Font.systemFont(11), MUTED);
    const d = row.addDate(new Date(data.last.at * 1000));
    d.applyRelativeStyle();
    d.font = Font.systemFont(11);
    d.textColor = MUTED;
  }
  if (!small && data.space) text(w, "🛰️ " + data.space, Font.systemFont(11), MUTED);
} else {
  for (const p of next.slice(0, small ? 1 : 3)) {
    const row = w.addStack();
    row.centerAlignContent();
    text(row, p.label, Font.boldSystemFont(small ? 17 : 15));
    row.addSpacer();
    if (p.at <= now + 5) {
      text(row, "overhead", Font.boldSystemFont(small ? 15 : 14), AMBER);
    } else {
      const d = row.addDate(new Date(p.at * 1000));
      d.applyTimerStyle();
      d.font = Font.boldMonospacedSystemFont(small ? 15 : 14);
      d.textColor = AMBER;
      d.rightAlignText();
    }
    const where = "look " + p.direction + ", " + p.up + "° up";
    text(w, [p.route || p.airline, where].filter(Boolean).join(" · "), Font.systemFont(11), MUTED);
    w.addSpacer(small ? 2 : 5);
  }
}
if (!small && data && data.updated && now - data.updated > 15 * 60) {
  text(w, "updated " + Math.round((now - data.updated) / 60) + " min ago", Font.systemFont(9), MUTED);
}

if (config.runsInWidget) {
  Script.setWidget(w);
} else {
  await w.presentMedium();
}
Script.complete();

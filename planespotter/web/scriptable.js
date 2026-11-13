// planespotter home-screen widget, for the free Scriptable app (https://scriptable.app).
// Shows the next planes due over your house, with a live countdown.
// Works while your iPhone is on your home Wi-Fi and planespotter is running.

const PLANESPOTTER = "__PLANESPOTTER_URL__";

const AMBER = new Color("#ffb020");
const MUTED = new Color("#9aa7b6");
const small = config.widgetFamily === "small";

async function load() {
  try {
    const req = new Request(PLANESPOTTER + "/api/next");
    req.timeoutInterval = 10;
    return await req.loadJSON();
  } catch (e) {
    return null;
  }
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
const w = new ListWidget();
w.backgroundColor = new Color("#0b0f14");
w.url = PLANESPOTTER;
w.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000);
w.setPadding(12, 14, 12, 14);

const head = w.addStack();
text(head, "✈ ", Font.boldSystemFont(12), AMBER);
text(head, data ? data.home : "planespotter", Font.boldSystemFont(12), AMBER);
w.addSpacer(6);

if (!data) {
  text(w, "Can't reach planespotter", Font.boldSystemFont(14));
  const t = w.addText("Is it running, and is this phone on your home Wi-Fi?");
  t.font = Font.systemFont(11);
  t.textColor = MUTED;
} else if (!data.next.length) {
  text(w, "Nothing due", Font.boldSystemFont(16));
  text(w, "in the next 15 minutes", Font.systemFont(12), MUTED);
  if (data.last) {
    w.addSpacer(4);
    const row = w.addStack();
    text(row, "Last: " + data.last.label + " · ", Font.systemFont(11), MUTED);
    const d = row.addDate(new Date(data.last.at * 1000));
    d.applyRelativeStyle();
    d.font = Font.systemFont(11);
    d.textColor = MUTED;
  }
} else {
  for (const p of data.next.slice(0, small ? 1 : 3)) {
    const row = w.addStack();
    row.centerAlignContent();
    text(row, p.label, Font.boldSystemFont(small ? 17 : 15));
    row.addSpacer();
    if (p.eta_s === 0) {
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
    if (small && p.sky) text(w, p.sky, Font.systemFont(10), MUTED);
    w.addSpacer(small ? 2 : 5);
  }
}

if (config.runsInWidget) {
  Script.setWidget(w);
} else {
  await w.presentMedium();
}
Script.complete();

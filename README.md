# planespotter ✈

Get an email whenever a plane flies over your house, plus a sky report
every morning with the last 24 hours of planes and the flights expected
overhead today, and a live 3D/2D map of the sky around you.

- **Alerts:** callsign, airline, route (e.g. *Singapore (SIN) → Los Angeles (LAX)*),
  aircraft type, a photo, altitude, distance, and a link to follow it live.
- **Quiet hours:** no alerts overnight. Those planes show up in the
  morning report instead, marked 🌙.
- **Daily report:** the last 24 hours of planes with stats (busiest hour,
  lowest, closest, top airlines), and a forecast of today's regular flights
  with their usual times.
- **Live map:** a 3D map (tilt and spin it, with buildings) or a flat 2D
  one, showing your zone, every plane nearby at its real altitude, and a
  searchable list of everything that has flown over. It runs on your Mac,
  so nothing is published, and your phone can open it too.
- Free data and no API keys. Nothing extra to install besides Python.

---

## Set it up on a Mac, step by step

### 1. Open Terminal

Press **⌘ Command + Space**, type **Terminal** and press **Return**. A
window with a text prompt opens. Each command below goes in there: copy
it, paste it with **⌘ V**, and press **Return**.

### 2. Check that Python is installed

```bash
python3 --version
```

- If it prints something like `Python 3.9.6` or higher, you're good.
- If a box pops up asking to install the **command line developer tools**,
  click **Install**, wait for it to finish (a few minutes), then run the
  command again.

### 3. Download planespotter into your home folder

This one command creates a folder called **planespotter** in your home
folder (the one with the house icon and your name, e.g. *HenryJohnson*)
and puts every file in it:

```bash
mkdir -p ~/planespotter && curl -fsSL https://github.com/JiraiyaGMS/2-hour-project-/archive/refs/heads/claude/exciting-cray-6oq525.tar.gz | tar -xz -C ~/planespotter --strip-components=1
```

To see it in Finder, press **⌘ Shift H** (that opens your home folder) and
open **planespotter**.

### 4. Go into the folder

```bash
cd ~/planespotter
```

> **This is the step that fixes "No such file or directory".** Terminal
> starts in your home folder, and the commands only work from inside the
> planespotter folder. Every time you open a new Terminal window, run
> `cd ~/planespotter` first. To check you're in the right place, run `ls`:
> you should see `README.md` and `planespotter` in the list.

### 5. Run the setup questions

```bash
python3 -m planespotter setup
```

It asks you:

1. **Your coordinates.** In Google Maps, right-click your house. The top
   line of the menu is two numbers (like `51.50071, -0.12462`). Click it
   to copy, then paste it into Terminal.
2. **A name** for the place, used in the emails (e.g. *Home*).
3. **The radius** in miles. 3 is a good start.
4. **A maximum altitude** (optional). Press Return for every plane, or type
   `10000` to only hear about low ones.
5. **Quiet hours.** Press Return to keep 22:00–07:00, type your own (like
   `11pm-6am`), or type `none`.
6. **The time for the daily report.** Press Return for 07:00.
7. **Your email address.** If it's Gmail, you also need an **app password**:
   - Go to <https://myaccount.google.com/security> and turn on
     **2-Step Verification** if it isn't already on.
   - Go to <https://myaccount.google.com/apppasswords>, type a name (e.g.
     *planespotter*) and click **Create**.
   - Copy the 16-letter password it shows and paste it into Terminal.
     **Nothing appears as you paste. That's normal (it's hidden).** Press Return.

   You can leave the email blank to try everything out first. Emails are
   then saved as files in `planespotter/outbox` instead of being sent.

At the end it offers to show the planes near you right now and to send a
test email. Say yes to both.

### 6. Start watching

Pick one:

- In Terminal (still inside the folder):

  ```bash
  python3 -m planespotter watch
  ```

- Or, in Finder, double-click **Start planespotter.command** in the
  planespotter folder.

Leave that window open (you can minimise it). It checks the sky every
20 seconds. To stop it, press **Control C** or close the window.

The live map opens at **http://localhost:8765** (the .command file opens it
for you). The first time, your Mac may ask *"Do you want the application
Python to accept incoming network connections?"* Click **Allow** if you
want to open the map on your phone too.

**Keep the Mac awake.** planespotter only watches while the Mac is
awake. *Start planespotter.command* stops the Mac from falling asleep
while it runs. In Terminal, you can do the same with
`caffeinate -i python3 -m planespotter watch`. Closing the lid of a laptop
still puts it to sleep.

### 7. (Optional) Start it automatically when you log in

Open **System Settings → General → Login Items**. Under **Open at Login**,
click **+**, go to your home folder → **planespotter**, choose
**Start planespotter.command**, and click **Open**.

---

## The live map

While planespotter is running, open **http://localhost:8765** in any
browser on your Mac.

- **3D / 2D** (top right): 3D tilts the map so you can see planes at
  their real height above the ground, with drop lines to show where they
  are over the map. To tilt and spin it, hold **Control** and drag (on a
  phone, drag up or down with two fingers, or twist). 2D is a flat view from
  straight above.
- **⌂** flies back to your house.
- **Your zone** is the orange circle. Planes inside it are orange, and the
  others nearby are blue. Planes glide between updates and leave a short
  trail.
- **In the air** lists every plane nearby right now, closest first.
  **Spotted** lists every plane that came through your zone. You can
  filter by 24 hours, 7 days, 30 days or all, and sort by newest, closest
  or lowest. 🌙 marks the ones that came during quiet hours.
- **Search** works on both lists and the map: try a flight number, an
  airline (*delta*), an airport code (*JFK*), a city, or an aircraft (*A380*).
- **Click any flight** (in the list or on the map) to see its route, a
  photo of the actual aircraft, its altitude and speed, and a link to
  replay its whole flight on adsb.lol.
- **🔥 Heat map** (top right) shows the busiest flight paths around you,
  from blue (quiet) to red (busy). Choose today, 7 days, 30 days or all
  time. It counts every plane within the map's range, not just the ones
  over your zone, so approach and departure routes stand out. The panel
  also lists the most common routes over your zone; click one to see
  those flights.

**On your phone:** when planespotter starts, it prints a second address
like `http://192.168.1.23:8765`. Open that on your phone while it's on
the same Wi-Fi. Add it to your home screen to use it like an app.

Flight paths are recorded from this version onwards. Flights spotted
before that show as a dot where they came closest. The heat map starts
from the flights already in your history and fills in as planespotter
keeps running. Give it a few days and the regular routes become clear.

**Just the map, no emails:** run `python3 -m planespotter map`. To turn the
map off, or to keep it to this Mac only, see the *Live map* settings in
[`.env.example`](.env.example).

---

## If something goes wrong

| What you see | What to do |
| --- | --- |
| `No such file or directory` or `No module named planespotter` | You're not in the folder. Run `cd ~/planespotter`, then try again. If that also fails, redo step 3. |
| `Settings problem: No home location set` | Run `python3 -m planespotter setup`. |
| `CERTIFICATE_VERIFY_FAILED` | Open **Finder → Applications → Python 3.x** and double-click **Install Certificates.command**. Then try again. |
| `Username and Password not accepted` | The app password is wrong. Make a new one (step 5) and run `setup` again. |
| No emails arriving | Look in your spam folder, and run `python3 -m planespotter test-email`. |
| Nothing ever shows up | Run `python3 -m planespotter check` to see what's flying nearby. Try a bigger radius, and check you pasted the coordinates of your house. |
| The map page won't load | planespotter has to be running. Check the Terminal window is still open, then reload the page. |
| The map won't open on my phone | The phone must be on the same Wi-Fi. Use the second address planespotter printed (not `localhost`). If your Mac asked about incoming connections and you clicked Deny, allow Python in **System Settings → Network → Firewall → Options**. |

**Change a setting:** run `python3 -m planespotter setup` again. Your
current answers show in [brackets], so just press Return on the ones you
want to keep.

**Get a newer version:** run the step 3 command again. Your settings and
history are kept.

**The settings file** is called `.env`. Finder hides files that start with
a dot; press **⌘ Shift .** in Finder to show them.

---

## Commands

Run these from inside the planespotter folder.

| Command | What it does |
| --- | --- |
| `python3 -m planespotter setup` | Asks a few questions and saves your settings. |
| `python3 -m planespotter check` | Lists the planes near you right now. Sends nothing. |
| `python3 -m planespotter watch` | Runs until you stop it: alerts, the daily report and the live map. Add `--open` to open the map in your browser. |
| `python3 -m planespotter map` | Just the live map, without any emails. |
| `python3 -m planespotter digest` | Sends the daily report right now. |
| `python3 -m planespotter test-email` | Sends a test email. |

There are more settings (how often to check, the hourly alert limit and so
on) in [`.env.example`](.env.example). Copy any of them into your `.env`.

## Other computers

**Raspberry Pi / Linux:** run steps 3–5 the same way. To keep it running
all the time and start it at boot, create
`/etc/systemd/system/planespotter.service`:

```ini
[Unit]
Description=planespotter
After=network-online.target
Wants=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/planespotter
ExecStart=/usr/bin/python3 -m planespotter watch
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now planespotter
```

**Windows:** install Python from <https://www.python.org/downloads/>
(tick *Add python.exe to PATH*). Download the code as a ZIP from GitHub,
unzip it, and open PowerShell in that folder. Use `python` instead of
`python3`, and run `pip install tzdata` once.

## How it works

- **Live positions** come from [adsb.lol](https://adsb.lol), a free
  community network of ADS-B receivers (data under ODbL). The watcher and
  the map share one request, at most every 10 seconds, and back off if
  adsb.lol says we're asking too often.
- **The heat map** splits the sky into squares about 280 m across. Each
  time a plane flies through a square, that square's count for the day
  goes up by one. Each plane counts once per square per trip, however
  slowly it flies, and the gaps between position reports are filled in so
  fast jets leave a continuous line.
- **The map** uses [MapLibre](https://maplibre.org) with free
  [OpenFreeMap](https://openfreemap.org) tiles, and [deck.gl](https://deck.gl)
  to draw planes in 3D. It's served only from your computer. It never
  serves your settings file or anything else from disk.
  **Routes and aircraft details** come from [adsbdb.com](https://www.adsbdb.com).
  Lookups are cached in a local database file (`planespotter.db`).
- **Catching fast planes.** A jet can cross a 3-mile zone in under a
  minute, so the watcher looks a few miles past the edge of your zone. It
  checks the straight line between each plane's last two positions, not
  just where the plane is now, so a plane that crossed between two checks
  still counts.
- **One email per plane.** Each plane gets one alert per trip over your
  house. If several planes arrive at once, they share one email.
  `MAX_ALERTS_PER_HOUR` (default 12) stops the alerts if you live under a
  busy flight path.
- **Quiet hours.** Planes are still recorded overnight, but no alerts are
  sent. The morning report covers the 24 hours since the previous report,
  so overnight planes appear there, marked 🌙.
- **Wrong routes are dropped.** Route databases map a flight number to a
  single leg, but many flight numbers fly several legs a day. A route is
  only shown if the plane is actually somewhere along it.
- **Today's forecast is learned.** Exact flight paths aren't published
  anywhere for free. But airlines fly the same flight number at about the
  same time every day, so any flight that has passed over on at least 2 of
  the last 7 days is listed at its usual time. The forecast starts after a
  couple of days of watching and gets better over the first week.

## Limits

- Coverage depends on volunteer receivers near you. Most populated areas
  are well covered; run `check` to see yours.
- Some military and privacy-blocked aircraft don't show up.

## Tests

```bash
python3 -m unittest discover -s tests
```

## Ideas for next time

- Push notifications (ntfy.sh, Telegram) alongside email.
- Only alert for interesting planes: helicopters, military aircraft,
  A380s, emergency squawks (7700).
- Busiest times of day: a chart of when planes come over.

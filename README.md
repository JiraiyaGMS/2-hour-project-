# planespotter ✈

Get an email whenever a plane flies over your house, plus a sky report
every morning with the last 24 hours of planes and the flights expected
overhead today, and a live 3D/2D map of the sky around you.

- **Alerts** by email and on your phone: callsign, airline logo, the route
  it's *really* flying (checked against its actual flight path), aircraft
  type, a photo, altitude, distance, and a link to follow it live.
- **Special planes:** separate alerts for emergencies, military aircraft,
  A380s and 747s, helicopters right overhead, and every airline or aircraft
  type you've never spotted before.
- **Quiet hours:** no alerts overnight. Those planes show up in the
  morning report instead, marked 🌙.
- **Daily report:** the last 24 hours of planes with stats (busiest hour,
  lowest, closest, top airlines), and a forecast of today's regular flights
  with their usual times.
- **Weekly email:** your week of planes, the rarest one, new additions to
  your collection, the busiest routes and airlines.
- **Live map:** a 3D map (tilt and spin it, with buildings) or a flat 2D
  one, showing your zone, every plane nearby at its real altitude, and a
  searchable list of everything that has flown over. It runs on your Mac,
  so nothing is published, and your phone can open it too.
- **Look up!** On the map, a card points to where in the sky to look for
  the plane coming over: which way to face and how high, with an optional
  chime.
- **Your collection** of airlines, aircraft types and countries, with
  badges and charts, and a **replay** of any day's traffic as a time-lapse.
- **Your own receiver** (optional): plug in a cheap ADS-B radio for
  unlimited, real-time data.
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
8. **Phone notifications** (optional). Answer **y** and follow
   [Phone notifications](#phone-notifications) below.
9. **An adsb.lol key** (optional). Press Return to skip; see
   [adsb.lol's daily limit](#adsblols-daily-limit-and-a-free-key) below for
   why you might want one.
10. **Your own receiver** (optional). Press Return to skip; see
    [Build your own receiver](#build-your-own-receiver-optional).

At the end it offers to show the planes near you right now, send a test
email, and send a test phone notification. Say yes to all of them.

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
- **Airline logos** show next to every flight in the lists, in the flight
  details, and above the labelled planes on the map. Airlines without a
  logo get a badge with their code, and private planes a small plane icon.
- **Whole flights:** every plane that crossed your zone is drawn along its
  whole flight. If it's still in the air, the line runs from takeoff to
  where it is right now (with the plane drawn there, updated every minute
  while the map is open). Once it lands, the line runs from takeoff to
  landing.
- **In the air** lists every plane nearby right now, closest first, then
  the ones that *went over you and are still flying* elsewhere.
  **Spotted** lists every plane that came through your zone, with
  *In the air* on the ones still flying. You can filter by 24 hours,
  7 days, 30 days or all, and sort by newest, closest or lowest. 🌙 marks
  the ones that came during quiet hours.
- **Search** works on both lists and the map: try a flight number, an
  airline (*delta*), an airport code (*JFK*), a city, or an aircraft (*A380*).
- **Click any flight** (in the list or on the map) to see its route, a
  photo of the actual aircraft, its altitude and speed, and its whole
  flight in pink. It also says whether the plane is still in the air (and
  where) or has landed. *Show its whole flight* zooms out to fit it. For
  any plane nearby, *Show where it's come from* shows its flight so far.
- **🔥 Heat map** (top right) shows the busiest flight paths, from blue
  (quiet) to red (busy). Choose today, 7 days, 30 days or all time.
  - Within about 15 miles it counts every plane, so approach and departure
    routes near you stand out.
  - Beyond that it shows the **complete flight** of every plane that
    crossed your zone. A Portland → LA flight that passed over draws
    a line all the way from Portland. *See the whole routes* zooms out to
    show them all.
  - The panel also lists the most common routes over your zone; click one
    to see those flights.

- **Look up!** When a plane is over your zone, or about to cross it, a card
  at the top of the map says which way to face and how high to look
  (*NE, 35° up*, or "about 3½ fists above the horizon": a fist at arm's
  length is about 10°), and how long until it's overhead. Tap it to select
  the plane. Turn on **🔔 Chime** (under the planespotter name) to hear a soft chime about a
  minute before each plane comes over. It works while the map is open.
- **✨ and 🚨 badges** mark special planes in the list: an A380, a military
  aircraft, a helicopter overhead, or an emergency.
- **Collection** (third tab) is everything you've ever spotted: totals,
  airlines with their logos, aircraft types, countries (from the planes'
  registrations), the times of day and days of the week planes come over,
  and 17 badges to earn (*Superjumbo*, *Night owl*, *Thousand club*...).
- **🕒 Replay** (top right) plays back a whole day of spotted flights as a
  time-lapse: pick the day and speed, drag the slider to any time.
- **Routes with a ✓** have been checked against the plane's real flight:
  the airport it actually took off from (and landed at), not just what a
  route database guessed from the flight number.

**On your phone:** when planespotter starts, it prints a second address
like `http://192.168.1.23:8765`. Open that on your phone while it's on
the same Wi-Fi. Add it to your home screen to use it like an app.

A plane's whole flight is first looked up while it's over your zone,
which shows where it came from. It's checked again 20 minutes, 1 hour, 3
hours and 6 hours later until it lands, sooner if adsb.lol sees it on
the ground. adsb.lol only keeps about the last day, so flights spotted
more than a day before you updated can't be filled in.

**Just the map, no emails:** run `python3 -m planespotter map`. To turn the
map off, or to keep it to this Mac only, see the *Live map* settings in
[`.env.example`](.env.example).

---

## Phone notifications

Alerts can go to your phone through **ntfy**, a free app with no account
needed. Tapping a notification opens the live map on that plane (when
your phone is on your home Wi-Fi).

1. Run `python3 -m planespotter setup`, press Return through the questions
   you've already answered, and answer **y** to *Send alerts to your
   phone?*. It shows a topic name like `planespotter-k3m9x2q7w4c8a1`.
2. On your phone, install **ntfy** from the App Store or Google Play.
3. Open ntfy, tap **+** (*Subscribe to topic*), type the topic name exactly,
   and tap **Subscribe**. Leave the server as `ntfy.sh`.
4. On iPhone, allow notifications when it asks.
5. Back in Terminal, send a test:

   ```bash
   python3 -m planespotter test-push
   ```

   A notification should arrive within a few seconds.

Anyone who knows your topic name could read your alerts, which is why it's
long and random. Keep it to yourself; the alerts never say where you live.

## Special planes

Special-plane alerts go out by email and phone, at most once every 6 hours
per plane, and not during quiet hours:

- **🚨 Emergencies:** a plane squawking 7700 (emergency), 7600 (radio
  failure) or 7500 (hijack), anywhere on the map (about 15 miles).
- **Military aircraft** anywhere on the map.
- **Rare and huge types** anywhere on the map: A380, 747, An-124,
  Dreamlifter, Beluga, C-5 Galaxy, B-52 and more. Change the list with
  `SPECIAL_TYPES` in `.env`.
- **Helicopters** right over your zone.
- **✨ Firsts:** the first time an airline or aircraft type comes over your
  zone, its normal alert says so. These start after a few days of
  watching, otherwise everything would be a first.

To see what one looks like, send a sample:

```bash
python3 -m planespotter test-special
```

Turn them off with `SPECIAL_ALERTS=false` (or just the helicopters with
`SPECIAL_HELICOPTERS=false`) in `.env`.

## The weekly email

Every Sunday at the daily-report time you get a summary of the week:
total planes compared with the week before, the busiest day and hour, the
lowest plane, the rarest plane, new additions to your collection, special
planes, and the busiest routes and airlines. Send one now to see it:

```bash
python3 -m planespotter weekly
```

Change the day with `WEEKLY_DAY` (e.g. `WEEKLY_DAY=fri`), or turn it off
with `WEEKLY_REPORT=false`.

## Build your own receiver (optional)

Planes broadcast their position on 1090 MHz, and a cheap radio can pick it
up directly. With your own receiver, planespotter gets every plane in
range instantly and without limits (checks every 5 seconds), and you can
share the data with adsb.lol for their 7,200-a-day feeder key.

**What you need (about $60–120):**

- A **Raspberry Pi 4 or 5** (or Pi 3B+), its power supply, and a
  **microSD card** (16 GB or more).
- An **ADS-B USB radio** tuned for 1090 MHz, e.g. the *FlightAware Pro Stick
  Plus* or *Nooelec NESDR SMArt*.
- A **1090 MHz antenna.** The one in the box works; a better one near a
  window, or outside and high up, sees much further.

**Set it up (about 30 minutes):**

1. Go to <https://adsb.im>, download the image for your Pi, and write it to
   the microSD card with **Raspberry Pi Imager** (choose *Use custom* and
   pick the downloaded file).
2. Put the card in the Pi, plug in the radio and antenna, connect the Pi to
   your router with a network cable (or set up Wi-Fi as adsb.im
   explains), and power it on.
3. After a few minutes, open <http://adsb-feeder.local> on your Mac and
   follow its setup: enter your location, and tick **adsb.lol** in the
   list of places to share data with.
4. Check it's working: <http://adsb-feeder.local:8080> shows a map of
   the planes your receiver can see.
5. Tell planespotter about it: run `python3 -m planespotter setup`, press
   Return through the earlier questions, and at **Your own ADS-B receiver**
   enter:

   ```
   http://adsb-feeder.local:8080/data/aircraft.json
   ```

   Setup checks it can read it, and says how many planes it can see.
6. Restart planespotter. The map's status now says *Live from your
   receiver*.

If the receiver ever stops answering, planespotter falls back to adsb.lol
on its own until it's back. For the feeder key, ask adsb.lol
(<https://www.adsb.lol/request>) once your receiver has been feeding for a
while, then add it in setup with `ADSB_DAILY_LIMIT=7200` in `.env`.

## adsb.lol's daily limit (and a free key)

The flight data comes free from [adsb.lol](https://adsb.lol), a community
network, which allows **500 requests a day** per home internet connection
without a key. planespotter shares those out across the day so the
alerts never run dry:

- **Checking for planes** gets most of them, so it checks about **every 4
  minutes**. A plane can cross your zone between checks; planespotter
  still catches it by joining up each plane's positions, but the alert
  can arrive a few minutes after it went over.
- **While the live map is open**, it checks **every 30 seconds** instead,
  until 60% of the day's allowance is used.
- **Whole flights** get a quarter of the allowance, and live positions
  for the map get a little.

You can raise the limit for free:

- **A free key** (1,000 a day: checks every 2 minutes). Ask for one at
  <https://www.adsb.lol/request>, then run `python3 -m planespotter setup`
  again and paste it at the last question.
- **Feeding adsb.lol** (7,200 a day: checks every 20 seconds). With a cheap
  ADS-B receiver (a USB radio stick and antenna on a Raspberry Pi) you
  share what planes overhead broadcast, and get a feeder key in return.
  Put the key in setup and add `ADSB_DAILY_LIMIT=7200` to your `.env`.

If the limit is ever reached anyway, the map says so and everything
carries on by itself once adsb.lol allows more requests.

---

## If something goes wrong

| What you see | What to do |
| --- | --- |
| `No such file or directory` or `No module named planespotter` | You're not in the folder. Run `cd ~/planespotter`, then try again. If that also fails, redo step 3. |
| `Settings problem: No home location set` | Run `python3 -m planespotter setup`. |
| `CERTIFICATE_VERIFY_FAILED` | Open **Finder → Applications → Python 3.x** and double-click **Install Certificates.command**. Then try again. |
| `Username and Password not accepted` | The app password is wrong. Make a new one (step 5) and run `setup` again. |
| No emails arriving | Look in your spam folder, and run `python3 -m planespotter test-email`. |
| No phone notifications | Run `python3 -m planespotter test-push`. Check the topic in the ntfy app matches `NTFY_TOPIC` in `.env` exactly, and that notifications are allowed for ntfy in your phone's settings. |
| Tapping a notification doesn't open the map | It opens the map's home-Wi-Fi address, so your phone must be on your home Wi-Fi and planespotter running. |
| No chime | Click **🔔 Chime** on the map first (browsers only play sound after a click), and keep the map open; check your volume. |
| *Your receiver isn't answering* | Open the receiver's address in a browser to check it's on, and check the address in setup. planespotter uses adsb.lol meanwhile. |
| The map says *adsb.lol's daily limit reached* | The free allowance is used up for now; it carries on by itself. A free key doubles it (see above). |
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
| `python3 -m planespotter weekly` | Sends the weekly email right now. |
| `python3 -m planespotter test-email` | Sends a test email. |
| `python3 -m planespotter test-push` | Sends a test phone notification. |
| `python3 -m planespotter test-special` | Sends a sample special-plane alert (email and phone). |

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

- **Live positions** come from [adsb.lol](https://adsb.lol)'s API, a free
  community network of ADS-B receivers (data under ODbL). The watcher and
  the map share each request, follow the daily budget above, and wait as
  long as adsb.lol asks if it ever says we're asking too often.
- **The heat map** splits the sky into squares about 280 m across. Each
  time a plane flies through a square, that square's count for the day
  goes up by one. Each plane counts once per square per trip, however
  slowly it flies, and the gaps between position reports are filled in so
  fast jets leave a continuous line.
- **Whole flights** come from adsb.lol's API (`/1/trace/full`), which
  returns an aircraft's recorded positions for about the last day.
  planespotter picks out the one flight that passed over you (a plane can
  fly several a day) and simplifies it to a few hundred points. Far from
  home these go on a coarser heat-map grid of about 4 km squares. Lookups
  run in the background, one at a time, so alerts are never held up.
- **Airline logos** come from the community
  [airline-logos](https://github.com/Jxck-S/airline-logos) collection, by
  the airline's three-letter code (the start of its callsign, like *DAL*
  for Delta). Each logo is downloaded once and kept in the `logo-cache`
  folder. Logos are trademarks of their airlines.
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
- **Routes are checked against the real flight.** Route databases map a
  flight number to a single leg, and are often wrong. So planespotter looks
  up the plane's actual flight as soon as it comes over (before the alert
  goes out, when the daily budget allows) and uses the airport nearest
  where it took off, and later where it landed or is about to. The
  airport list (about 12,000 airports, from the public-domain
  [OurAirports](https://ourairports.com/data/)) comes with planespotter;
  `python3 scripts/update_airports.py` refreshes it.
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

- Point your phone at the sky: an augmented-reality view of the planes.
- A tiny e-ink or LED display by the window showing the next plane over.
- Sound: hear each plane's flight number read out as it comes over.

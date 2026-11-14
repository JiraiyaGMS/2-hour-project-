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
- **Quiet hours:** nothing on your phone overnight; notifications start
  again with the first plane after. Night planes show up in the morning
  report instead, marked 🌙. The widget, Siri and the phone map work all
  the time.
- **Daily report:** the last 24 hours of planes with stats (busiest hour,
  lowest, closest, top airlines, the plane of the day), and a forecast of
  today's regular flights with their usual times.
- **Weekly email:** your week of planes, the rarest one, new additions to
  your collection, the busiest routes and airlines.
- **Live map:** a 3D map (tilt and spin it, with buildings) or a flat 2D
  one, showing your zone, every plane nearby at its real altitude, and a
  searchable list of everything that has flown over. It runs on your Mac,
  so nothing is published, and your phone can open it too.
- **Look up!** On the map, a card points to where in the sky to look for
  the plane coming over: which way to face and how high, with an optional
  chime or a **voice** that reads out each plane. It also says when the
  **clouds** or the dark will hide it, from the nearest airport's weather.
- **Coming over next:** the next few planes heading for your zone, with
  countdowns.
- **Sky view (AR):** hold your phone up and every plane is labelled where
  it is in the sky.
- **Your collection** of airlines, aircraft types and countries, with
  badges, charts, **records** (lowest, fastest, longest flight...), a
  **plane of the day**, and a streak for planes you've spotted with your own
  eyes. Add **your own photos** to flights, and make a **share card** of any
  flight to text to friends. Plus a **replay** of any day as a time-lapse.
- **Noise log:** low and late-night flights, a **noise meter** that uses
  your Mac's microphone to rank the loudest planes, and a spreadsheet you
  can download.
- **Your own receiver** (optional): plug in a cheap ADS-B radio for
  unlimited, real-time data.
- **Offline log:** when planespotter wasn't running (Mac asleep, switched
  off), it looks up the planes that came over meanwhile, up to about 20
  hours back, and puts them in your reports marked 📴.
- **Games:** a weekly **spotting bingo** card, **guess the plane**, **your
  year (or month) in planes**, and a **profile** of every aircraft you've seen.
- **Smarter predictions:** which way your airport is **landing today** and
  what that means for you, whether your regular flights are **late or
  early**, a **rush alert** when lots of planes are about to come over, and
  **follow a flight** to get told when it lands.
- **On your phone:** *"Hey Siri, Planespotter"*, a **home-screen
  widget** with a countdown, and **kiosk mode** for an old tablet or a TV.
- **Space station passes:** when the ISS will fly over and be visible, with
  a notification beforehand.
- **More places:** watch your work or a relative's house too, and save a
  day's **replay as a video**.
- **Anywhere, on its own:** the widget and Siri find the planes around
  wherever your phone is, even with the Mac off. Your **plane alerts follow
  your phone** when you travel, **My flight** follows your flight, your
  **family** gets the highlights, and **friends** can compare collections.
- **Always on:** planespotter runs **by itself**, on your Mac or on a
  **free cloud server** (so the alerts keep coming with the Mac closed), you
  can switch **emails off** and get the reports as phone notifications, and
  a **live map on your phone** shows the planes around you anywhere.
- **Sky extras:** a heads-up when a plane is about to **cross the Moon**,
  planets and bright stars in the sky view, a **contrail forecast**, and the
  **best time to look up** each day, in your **calendar** too.
- **History:** replay **any day** in the time machine, and add a batch of
  photos that **match themselves** to the right planes.
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

### 7. Let it run by itself (recommended)

So you never have to start it again:

```bash
python3 -m planespotter autostart
```

planespotter then runs in the background all the time: from when you log
in, and again within a minute if it ever stops. You can close Terminal. See
*Always on* below.

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
  and 20 badges to earn (*Superjumbo*, *Night owl*, *Thousand club*...).
- **🕒 Replay** (top right) plays back a whole day of spotted flights as a
  time-lapse: pick the day and speed, drag the slider to any time.
- **Routes with a ✓** have been checked against the plane's real flight:
  the airport it actually took off from (and landed at), not just what a
  route database guessed from the flight number.
- **The weather line** under your house's name says what the sky is doing
  (*☁️ Overcast at 1,800 ft*, *🌙 Clear night*), from the nearest airport's
  weather report. When a plane will be above the clouds, the Look up! card
  (and the alert) says *Above the overcast at 1,800 ft: listen for it*.
  After dark it says to look for its lights, and it warns you if the plane
  is near the sun.
- **Coming over next** (top of *In the air*) lists the next few planes
  heading for your zone with a countdown, if they keep flying straight.
- **🗣️ Voice** (next to Chime) reads out each plane about 45 seconds before
  it comes over: *"Delta flight 573 from Fort Lauderdale, coming over in
  about 30 seconds. Look north-east, 25 degrees up."* It stays quiet during
  quiet hours.
- **👀 I saw it:** tap it on the Look up! card (or a flight's card) when you
  actually spot the plane in the sky. Do it every day to build a **streak**.
- **📷 Add photo** on any flight's card keeps your own picture with it (taken
  on your phone, or picked from your photos). It's shrunk to a sensible
  size, saved on your Mac in the `photos` folder, and shown in the flight's
  card and in *Collection → Your photos*. Tap a photo to see it big,
  download it or delete it.
- **↗ Share** makes a picture of the flight (its route drawn out, airline,
  aircraft, when you saw it, and your photo if you added one) to text to
  friends. On a phone it opens the share sheet (with the secure address,
  see *Sky view* below); elsewhere, save the picture and send it. The
  card never shows where you live.
- **Collection** also shows the **plane of the day** (the rarest or most
  special plane today, like *"Only the 2nd Boeing 747-8 you've spotted"*;
  it's in the morning email too), your streak, and your **records**:
  lowest, closest, fastest, highest, longest flight, the plane you see most
  (your *old friend*), and the busiest day and hour. Tap one to see it.
- **Noise** (fourth tab): see *Noise log* below.
- **🛬 The runway line** under the weather says which way your nearest big
  airport is landing (*LAX is landing towards the west, into a 12 kt west
  wind*). After a few days of watching it adds what that means for you:
  *about 6 planes an hour over you (1 an hour when it lands towards the east)*.
  Airports land into the wind, so when the wind changes, so does your sky.
- **Your regular flights today** (bottom of *In the air*): the flights that
  come over most days, with their usual time and whether today's was on
  time, late, early, still due, or hasn't shown up. Spotted flights by
  regulars get an *on time* / *12 min late* badge too.
- **🛰️ Space station and friends** (bottom of *In the air*): the next times
  the space station (and China's Tiangong and Hubble) cross your sky where
  you can see them. While one is overhead, the Look up! card points to it.
- **🎯 Guess** on the Look up! card: before you look it up, guess what kind
  of plane it is (Boeing 737, Airbus A320 family, E-Jet...) from four
  choices. Your score is in Collection.
- **🔔 Follow** on a plane that's flying: your phone gets a notification
  when it lands, wherever that is (*DAL573 has landed in Atlanta (ATL)*).
  Needs phone notifications set up.
- **✈ This plane** on any flight's card: its profile. Every time you've
  seen that aircraft, its routes, and the time it usually comes over.
  *Collection → Old friends* lists the aircraft you see most.
- **🎱 Spotting bingo** (Collection): a new card every Monday with 8 things to
  spot (a 787, a cargo plane, a plane before 7 am...) picked from what flies
  near you, plus a free square. Squares fill in by themselves; your score
  is in the weekly email.
- **✨ Your year in planes** and **this month in planes** (Collection): your
  totals, top airline and aircraft, the farthest-away airport, the rarest
  plane, busiest day, and a chart, with **↗ Share** for a picture.
- **📴** marks planes the offline log found (see *The offline log* below).
- **🎬 Save video** (in Replay): plays the day from the first plane to the
  last and saves it as a video to share.
- **Other places** (if you set some up): a picker next to your house's name
  switches to that place's map.

**On your phone:** when planespotter starts, it prints a second address
like `http://192.168.1.23:8765`. Open that on your phone while it's on
the same Wi-Fi. Add it to your home screen to use it like an app. It also
prints a secure address like `https://192.168.1.23:8766` for the sky view,
the share sheet and the microphone (see below).

A plane's whole flight is first looked up while it's over your zone,
which shows where it came from. It's checked again 20 minutes, 1 hour, 3
hours and 6 hours later until it lands, sooner if adsb.lol sees it on
the ground. adsb.lol only keeps about the last day, so flights spotted
more than a day before you updated can't be filled in.

**Just the map, no emails:** run `python3 -m planespotter map`. To turn the
map off, or to keep it to this Mac only, see the *Live map* settings in
[`.env.example`](.env.example).

---

## Sky view (AR) on your phone

Hold your phone up to the sky and every plane is labelled right where it
is, with its flight number, route, height and distance. Planes over your
zone are orange; arrows at the edge point to ones you're not facing. Tap a
label for details and *I saw it*.

Phones only let a web page use the camera and compass over a secure
(https) connection, so planespotter runs a secure copy of the map too. It
makes its own certificate on your Mac. Nobody vouches for a home-made
certificate, so your phone warns you **the first time**:

1. Start planespotter. It prints a line like
   `AR sky view (phone):   https://192.168.1.23:8766/sky`.
2. On your phone (on the same Wi-Fi), open that address. Or open the map and
   tap the **phone-with-a-star button** (top right).
3. **iPhone (Safari):** you'll see *This Connection Is Not Private*. Tap
   **Show Details**, then **visit this website**, then **Visit Website**.
   **Android (Chrome):** you'll see *Your connection is not private*. Tap
   **Advanced**, then **Proceed to 192.168.1.23 (unsafe)**.
   This is safe here: the certificate was made by planespotter on your own
   Mac, and the connection never leaves your Wi-Fi.
4. Tap **Start**, then **Allow** for the camera, and **Allow** for motion
   and orientation.
5. Hold the phone up (upright, like taking a photo) and look around.

Tips:

- **Labels a bit to one side?** Phone compasses are often off by 10° or
  so, especially near metal or a car. Drag sideways to line the labels up
  with a plane you can see. It remembers.
- **Labels too spread out or bunched up?** Use **+** and **−**. Phones'
  cameras differ.
- On an iPhone, check **Settings → Privacy & Security → Location Services
  → System Services → Compass Calibration** is on.
- It assumes you're at home (it uses your house's location). It works a
  few streets away too.
- On a computer there's no compass or camera, so it shows the sky as a
  chart you can drag around. Handy for a look.

This view is **experimental**: it depends on your phone's compass and
camera, and how well it lines up varies from phone to phone.

To turn the secure address off, set `MAP_HTTPS=false` in `.env`.

## Noise log

The **Noise** tab on the map tracks low and late-night flights:

- How many planes came over your zone, how many were **low** (under
  2,000 ft above you), and how many came **at night** (your quiet hours,
  or 11 pm–7 am if you don't have any), for the last 7, 30 or 90 days.
- A chart of planes per night, and lists of the lowest planes and the latest
  night ones. Tap any of them to see the flight.
- **Download a spreadsheet** of every plane over your zone: date, time,
  flight, airline, aircraft, route, how close and how low it came, low/night,
  and measured loudness. It opens in Numbers, Excel or Google Sheets. Handy
  if you want to send a complaint to an airport or your council.

### The noise meter

The noise meter uses your Mac's microphone to measure how loud each plane
is as it flies over, and ranks the loudest.

1. On the Mac running planespotter, open **http://localhost:8765** (Chrome
   works best).
2. Click the **Noise** tab, then **Start listening**.
3. Click **Allow** when the browser asks to use the microphone. If macOS
   asks too, allow it, or turn the browser on under **System Settings →
   Privacy & Security → Microphone**.
4. Leave that tab open. It can be behind other windows, but the Mac needs
   to stay awake. The status line says *🎙️ listening*.

About 3 minutes after a plane goes over your zone, it gets a loudness
reading: how many decibels louder it was than the usual background noise
just before and after. It appears in *Loudest planes*, in the flight's
card (*🎙️ +18 dB louder than the background*) and in the spreadsheet.
Microphones aren't calibrated, so this is a fair comparison between
planes, not an official noise level. A barking dog at the same moment
counts too.

Nothing is recorded: the page measures the level once a second, and only
that number is sent to planespotter, which keeps it for 3 days. The noise
meter only works on the Mac itself (or on the secure https address);
browsers block the microphone on plain addresses.

## The offline log

planespotter can only watch while it's running. When it starts again (or the
Mac wakes up), it works out how long it was off and looks the gap up in
adsb.lol's records:

1. It asks adsb.lol for every plane within 250 nautical miles right now,
   including ones parked at nearby airports.
2. It picks the ones that could have come over: planes whose course leads
   back past your house, planes that landed nearby, and planes you see
   often.
3. It looks up each one's recorded track and keeps every time it crossed
   your zone while planespotter was off.

These planes go in the daily and weekly emails, the map, the heat map and
your collection, marked **📴 found while offline**. They don't get an alert
each (they're long gone), but your phone gets one summary: *📴 23 planes
came over while planespotter was off*. The weekly email has a section
listing them and where they came from.

You don't need to do anything: it happens by itself. Good to know:

- adsb.lol only keeps about the **last 20 hours** of each plane. If
  planespotter is off for longer, only the last 20 hours can be filled in
  (the weekly email says how much was missed).
- It can't find planes that have since landed far away or flown out of
  range, so it finds most planes, not all. To catch everything, keep the
  Mac awake while it runs, or run planespotter on a Raspberry Pi (see
  *Other computers*).
- It uses up to a fifth of the day's adsb.lol allowance, and slows down if
  adsb.lol asks it to.

## Always on

Three things make planespotter something you just check on your phone:

1. **The Mac runs it by itself.** `python3 -m planespotter autostart` sets it
   up once (a macOS LaunchAgent): it starts when you log in, restarts within
   a minute if it ever stops, and keeps the Mac awake while it's plugged in.
   It can never run twice: if you also type `python3 -m planespotter watch`,
   that one just says it's already running. Check on it with
   `python3 -m planespotter autostart status` (it shows the latest
   messages, which are kept in `planespotter.log`), and stop it with
   `python3 -m planespotter autostart off`.
2. **The widget and Siri work on their own.** The planespotter script on
   your phone gets your location and asks adsb.lol which planes are about to
   come over, right then, from the phone's own connection (it doesn't use up
   your Mac's daily allowance). So they work at home, at a hotel, on mobile
   data, and with the Mac off or asleep. At home they also show what the
   Mac knows: your regular flights, the last plane over, the last hour.
   If there's no internet, they show the last answer they got.
3. **Your alerts follow you.** When your phone has moved a couple of miles,
   the script tells planespotter on your Mac where it is (through your
   private ntfy command topic). Once you're more than 5 miles from home (and
   your other places), the Mac watches where you are too, moves the zone as
   you go, and switches back when you're home: you get one notification when
   it starts and one when you're back. Turn it off with `FOLLOW_PHONE=off`.

**Push notifications need planespotter running somewhere.** An iPhone
can't run a program in the background all day, so the alerts come from
planespotter on a computer that's always on. On your Mac, they pause while
it's asleep (a MacBook with its lid closed sleeps) and the offline log fills
in what it missed. For alerts all the time with the Mac closed or far away,
**run it on a free cloud server** (next section): one command moves it
there. The widget, Siri and the phone map never need either.

## Run it in the cloud (free, always on)

A cloud server is a computer in a data centre that's always on. Oracle
Cloud gives everyone one small server free, for good, which is plenty for
planespotter. Once it runs there, your Mac can be closed, asleep or with
you anywhere: the alerts, the morning and weekly reports, and the
alerts-follow-your-phone part all keep going.

**1. Make a key on your Mac** (it lets your Mac log in to the server). In
Terminal:

```bash
ssh-keygen -t ed25519
```

Press **Return** three times. (If it says the file *already exists*, type
**n**: you already have a key.) Then copy the public half of the key (it's
safe to share):

```bash
pbcopy < ~/.ssh/id_ed25519.pub
```

**2. Sign up** at **oracle.com/cloud/free** → **Start for free**. Choose a
**Home Region** near you (for example *US West (San Jose)*); it can't be
changed later. Oracle asks for a card to check you're a real person: the
Always Free server isn't charged.

**3. Make the server.** In the Oracle Cloud console, open the **☰** menu →
**Compute** → **Instances** → **Create instance**. (The names below can
differ a little as Oracle updates its pages.)

- **Name:** `planespotter`.
- **Image and shape** → **Edit**:
  - **Change image** → **Ubuntu** → **Canonical Ubuntu 24.04** → **Select**.
  - **Change shape** → **Virtual machine** → **Specialty and previous
    generation** → **VM.Standard.E2.1.Micro** (marked *Always Free-eligible*)
    → **Select**.
- **Networking:** leave it as it is (it gets a public IP address).
- **Add SSH keys:** choose **Paste public keys** and paste (**Command V**).
- Click **Create**.

**4.** When it says **Running** (a minute or two), copy its **Public IP
address** from the page.

**5. Move planespotter there.** On your Mac (put the address in place of
`203.0.113.5`):

```bash
cd ~/planespotter && python3 -m planespotter server ubuntu@203.0.113.5
```

It installs planespotter on the server, copies your settings (with your
time zone: servers run on UTC) and your history, starts it there so it runs
by itself from boot, and stops it on your Mac so every alert comes once.
When it says **✔ planespotter now runs on your server**, you're done. Your
phone needs nothing new.

| Command (on your Mac) | What it does |
| --- | --- |
| `python3 -m planespotter server status` | Is it running there, and its latest messages. |
| `python3 -m planespotter server update` | After updating planespotter on your Mac, update it on the server too. |
| `python3 -m planespotter server stop` | Stop it on the server and bring its history back to the Mac (then `autostart` runs it on the Mac again). |

Good to know:

- While the server runs it, `watch` on the Mac says so and doesn't start
  (every alert would come twice). The live map on the Mac
  (`python3 -m planespotter map`) still works for the planes in the sky; your
  history is on the server.
- Oracle says it may reclaim Always Free servers that sit almost idle for a
  week. planespotter is light, so if that ever happens, make a new server
  and run the `server` command again.
- Any always-on Linux computer works the same way (another cloud, a
  Raspberry Pi at home): `python3 -m planespotter server user@address`.

### Emails off

```bash
python3 -m planespotter emails off
```

No more emails (or saved email files). Plane alerts were already on your
phone; the morning report and the weekly report (including what the
offline log found) now come as phone notifications too. `emails on` brings
them back. Restart planespotter afterwards (if it runs by itself: run
`python3 -m planespotter autostart` again).

## Your phone setup kit (widget, Siri, commands, family, friends)

Once phone notifications are set up (see *Phone notifications*), one command
emails you everything for your phone, so you can set it up from anywhere:

```bash
python3 -m planespotter phone
```

The email has the widget script attached, and links and short steps for
each part below. (It's also saved in the `phone-setup` folder.)

The widget and Siri **work anywhere, on their own**: the script finds the
planes around wherever your phone is, straight from adsb.lol (see *Always
on*). At home it adds what your Mac knows, from a small status message
planespotter posts to a private ntfy topic (your regular flights, the last
plane over, the space station...). planespotter only posts a new one when
something changes, spread evenly through the day.

Your phone works out what's true *when you look or ask*: planes whose time
has passed are dropped, and the countdowns tick by themselves.

### Home-screen widget (iPhone)

A widget with the next planes due over your house and a live countdown:

1. Install the free **Scriptable** app from the App Store.
2. On your iPhone, open the email from `python3 -m planespotter phone`. Copy
   the whole script at the bottom (press and hold, **Select All**, **Copy**),
   or open the attached `planespotter-widget.js`.
3. Open **Scriptable**, tap **+**, and paste. Tap the title at the top and
   name it **planespotter**. Tap **▶** to try it, then **Done**. When it
   asks to use your location, tap **Allow While Using App**.
4. In the iPhone's **Settings → Scriptable → Location**, choose **While
   Using the App or Widgets**, and turn **Precise Location** on (so it knows
   which planes are over *you*).
5. Go to your home screen, press and hold an empty spot, tap **Edit** (or
   **+**), then **Add Widget**, search for **Scriptable**, choose the small
   or medium size, and tap **Add Widget**.
6. Press and hold the new widget, tap **Edit Widget**, and set **Script**
   to **planespotter**.

**Tap the widget for a live map** of the planes around you (see *Live map
on your phone* below).

What it shows:

- **Planes due in the next few minutes**, with a countdown, a route and
  where to look.
- **Your next regular flight** when nothing is due yet (a flight that comes
  over at about the same time most days), counting down to its usual time.
- **The last plane over** (how long ago) and **how many came over in the
  last hour** (at home), or **how many planes are within 10 miles** (away).
- At the top: **Home**, or the town you're in.

iPhone decides when widgets refresh (often only every 15 to 30 minutes), so
the countdowns tick by themselves in between. Tapping the widget opens the
map (on your home Wi-Fi).

To update the widget after updating planespotter, the easiest way is
`python3 -m planespotter phone --install` on your Mac. It puts the script
straight into Scriptable's folder in iCloud Drive (the Mac and iPhone need the
same Apple ID with iCloud Drive on), and iCloud brings it to the phone within
a minute or so. It replaces every planespotter script there, keeps the icon,
and saves the old one in the `phone-setup` folder. Or copy and paste:
`python3 -m planespotter phone --copy widget` on your Mac, then in Scriptable
open **planespotter**, select all, paste over it, and tap **Done**.

**Did it update?** Tap the widget: on the map, the Point button reads
**🔭 Point ↗**. If it says just **🔭 Point** (and Point says *The compass
wasn't allowed*), your phone still has the old script.

### Live map on your phone

Tap the widget (or open **planespotter** in Scriptable and tap **▶**) for a
full-screen map of the planes around you, wherever you are: your zone around
the blue dot, every plane with its callsign and height (amber: the next
ones over you, blue: below 10,000 ft), moving smoothly between updates, and
**Next over you** with countdowns at the bottom. Tap a plane for its airline,
route, aircraft type, height and speed. Close it with **Close** (top left) or
a swipe down.

It asks adsb.lol from your phone every 15 seconds for the first 10 minutes,
then every minute, and pauses for the day if the phone has asked about 400
times (adsb.lol allows about 500 a day per connection; the widget, Siri and
searches share that). It doesn't need your Mac or the server.

**✈︎ Track a flight.** Tap it and type a flight number, an airline, an
airport, or a mix, then **Search**:

| You type | You get |
| --- | --- |
| `UA 1234`, `UAL1234`, `United 1234` | That flight, wherever it is in the world |
| `N12345` | That plane (by its registration) |
| `Southwest` | Southwest planes near you, nearest first |
| `LAX`, `Los Angeles`, `Heathrow`, `John Wayne` | Planes near that airport: ones flying to or from it first |
| `Southwest LAX`, `Delta JFK` | That airline's planes near that airport |

A flight that isn't in the air yet shows **📍 Track**: it's pinned at its
airport (or where it is on the ground, if it's taxiing), with its gate, a
countdown to take-off and how late it's running (those three need gate
numbers set up; **↻ Times** checks again), and the map switches to following
it as soon as it takes off.

The best 10 show first; **Load more** shows 10 more. Tap one to track it: the
map follows it (green, with its trail) wherever it goes, and a card shows its
route, height, speed and how far it is from you. **🔔 Notify me when it lands**
asks planespotter on your Mac (or server) to follow it too, so you get
notifications when it takes off, flies over home and lands. A flight that
isn't in the air yet shows **🔔 Tell me when it flies**. Airlines and airports
are found by searching the sky around you or the airport (adsb.lol has no
worldwide list), and flight numbers worldwide. The first time you search for
a small airport, the phone downloads the full airport list once (about 0.8
MB) and keeps it.

**🔭 Point.** Tap **Point**: Safari opens with a map of the planes around
you and a blue dot for you. Tap **Start**, and the first time tap **Allow**
when Safari asks about motion and orientation. A blue beam, like the one in
Life360, shows which way your phone is facing. Hold the phone flat in front of
you and point its top at a plane: the plane inside the beam is picked (it
turns orange) and you're told which plane it is, out loud too (tap **🔊** to
mute), with its route, height and distance. If there's no plane in the beam,
it tells you which way to turn, and how many degrees, to the next plane coming
over you (or the one highest in the sky).

It's in Safari because Scriptable can't use the compass. The planes are
the ones planespotter just got: they keep moving on the map by their speed
and heading, and after 3 minutes it asks you to tap **↻ Fresh planes**, which
goes back to Scriptable for new ones (Safari asks *Open in Scriptable?*: tap
**Open**). Siri itself can't tell which way your phone is facing. For a Siri
shortcut straight to it, make a second shortcut with one action, **Open
URLs**, set to `scriptable:///run/planespotter?point=1`, and name it **Point
Planespotter**. Then say *"Hey Siri, Point Planespotter"*: it opens Safari
with the planes, and you tap **Start**.

### Siri: "Hey Siri, Planespotter"

Siri answers: *"Air Canada flight 791 from Montreal is due in about 4
minutes, look to the east. Then Delta flight 573 from Atlanta in about 9
minutes."* When nothing is due, it tells you where the nearest plane is and
which way to look (*"The nearest plane is Southwest flight 1672, about 6 miles
away: look north-east, about 9° up"*), your next regular flight and the last
plane over.

Siri uses the planespotter script in Scriptable, so set up the widget first
(above). Then:

1. Open the **Shortcuts** app and tap **+**.
2. Tap **Add Action** (or the search bar at the bottom), type **Run Script**,
   and choose the one from **Scriptable**. Tap **Script** and choose
   **planespotter**. Then tap the small arrow **›** on that step (or **Show
   More**) and turn **Show When Run** off, so Siri answers straight away
   instead of waiting for you to tap **Done**.
3. Search for **Speak Text** and add it underneath. It should say **Speak
   Script Result**. If the blank isn't filled in, tap it and choose **Script
   Result**.
4. Tap the name at the top, rename the shortcut **Planespotter** (a name
   that isn't an everyday phrase, or Siri answers the question itself), and
   tap **Done**.
5. Say *"Hey Siri, Planespotter"*.

Older shortcuts that read the status link directly (Get Contents of URL,
key `siri`) still work, but they can mention a plane that has already gone
by. Switch to the steps above.

### Siri: "Hey Siri, where's my plane?"

Tells you where **your** flight is: the one you last picked in **✈︎ Track a
flight** on the phone map (or, if you haven't picked one, the one
planespotter on your Mac is following).

- In the air: *"Delta flight 783 is in the air about 80 miles north-east of
  Las Vegas, at 36,000 feet, heading to Seattle, about 1 hour 50 minutes
  away."* (and *"It's coming down to land"* near the end).
- On the ground before take-off: *"… is on the ground at Los Angeles,
  taxiing."*
- Landed: *"Alaska flight 312 has landed in Seattle, about 25 minutes
  ago."* It works out where and when from the plane's recorded positions
  (adsb.lol keeps about a day of them), and remembers it after that
  (*"about 2 days ago"*).
- Gates: *"It's at gate N12, terminal N. Bags come out at carousel 12."* if
  you've set up gate numbers (below), otherwise *"No known gate."*

Set it up like the first Siri shortcut, with one difference:

1. In **Shortcuts**, tap **+**, add **Run Script** (Scriptable), and choose
   **planespotter**.
2. Tap the arrow **›** on that step: turn **Show When Run** off, and in
   **Parameter** (some versions call it **Texts**) type `my plane`.
3. Add **Speak Text** underneath.
4. Name the shortcut **Where's my plane** and tap **Done**. Then: *"Hey Siri,
   where's my plane?"*

### Siri: "Hey Siri, find a flight"

Any flight, not just yours. iPhones don't let a Siri shortcut take a flight
number in the same sentence as its name, so Siri asks: *"Hey Siri, find a
flight"* → *"Which flight?"* → *"Alaska 1349"* (or *"A S A 1349"*, *"UA 12"*).

- In the air: *"Alaska flight 1349 is in the air about 40 miles north-west of
  you, near Santa Barbara, at 34,000 feet, heading to Seattle, about 1 hour
  50 minutes away. Look north-west, about 8° up."* (the last part when it's
  close enough to see).
- Out of sight: *"… isn't showing right now. It was last seen about 40
  minutes ago, about 1,140 miles west of San Francisco, at 36,000 feet:
  probably out of range of the receivers (over an ocean, say)."*
- Landed: where and how long ago, and the gate if known.
- Not taken off yet (with gate numbers set up): *"… hasn't taken off yet. It
  leaves Seattle from gate C3, taking off in about 2 hours 20 minutes. It's
  running 25 minutes late."*

**Where the answers come from, and how much to trust them.** The live
position comes from adsb.lol's volunteer receivers: exact and seconds old,
but they don't cover everywhere (oceans, and quiet stretches like rural
Montana). FlightAware also uses radar and satellites, so with gate numbers
set up (FlightAware AeroAPI is best for this), planespotter asks it whenever
the free receivers can't see the flight, and says so: *"FlightAware says
Alaska flight 1349 is in the air: it took off from Portland about 2 hours
ago … Its last reported position was over Montana …"*. A flight number flies
every day, often on a different aircraft, so planespotter only trusts what
it saw of a flight in the last 12 hours (or since you picked it), never
yesterday's trip. Route databases (adsbdb) are often out of date, so they're
only used for planes that are flying and only when the route fits where the
plane actually is.

To set it up:

1. In **Shortcuts**, tap **+** and add **Ask for Input**: type of input
   **Text**, prompt `Which flight?`.
2. Add **Run Script** (Scriptable), choose **planespotter**, tap the arrow
   **›**, turn **Show When Run** off, and set **Parameter** (or **Texts**) to
   **Provided Input** (tap the field, then the variable above the keyboard).
3. Add **Speak Text** underneath.
4. Name it **Find a flight** and tap **Done**.

### Gate numbers (optional, free with your own key)

The plane data planespotter uses has no gates: those come from airline and
airport schedules, through flight-status services. Two have a free monthly
allowance; pick one:

| Service | Free allowance | Sign up |
| --- | --- | --- |
| **FlightAware AeroAPI** (Personal) | up to $5 of credit a month, and a lookup costs $0.005: about 1,000 lookups | flightaware.com/aeroapi: sign in, choose **Personal**, and make an API key (it may ask for a card; personal use only) |
| **Aviationstack** (Free) | 100 lookups a month | aviationstack.com: **Sign up free**, then copy **Your API Access Key** from the dashboard |

Then, on your Mac:

```bash
python3 -m planespotter gates aeroapi YOUR_KEY
```

(or `gates aviationstack YOUR_KEY`), check it works with
`python3 -m planespotter gates test UAL1`, and copy the script to your phone
again (`python3 -m planespotter phone --copy widget`).

Gates are looked up only when you ask: tap **🚪 Gate** on a plane or on the
flight you're tracking, or ask *"Hey Siri, where's my plane?"*. Each answer is
kept on your phone for two hours, so asking again costs nothing, and the
phone stops just under the free allowance each month (900 lookups for
AeroAPI, 90 for Aviationstack; change it with `GATES_MONTHLY`). Your key goes
only into the copy of the script for your phone, not the one your Mac's map
serves on your Wi-Fi. `gates off` turns it off. Not every airport reports
gates, so some flights still say *no known gate*.

### Commands from your phone

Your phone can tell planespotter what to do, from anywhere, through a second
private ntfy topic. Open the *Commands page* link from the phone setup email
in Safari (bookmark it), type a message and send it:

| Send | What it does |
| --- | --- |
| `travel 33.8090,-117.9265 Disneyland` | Travel mode: watch there for two days (see below) |
| `home` | Stop travel mode |
| `flight UA1234` (or `flight UA1234 tomorrow`) | Follow your flight (see *My flight*) |
| `flights off` | Stop following flights |
| `status` | What planespotter is doing |

planespotter answers in your ntfy app within a minute. The email also
explains a **Spot from here** shortcut that sends your phone's location
with one tap.

### Travel mode

Watch wherever you are (a hotel, a friend's house, an airport) as well as
home: its own zone, alerts and map (on port 8774). **It starts by itself**
when your phone is more than 5 miles from home (see *Always on*), follows
you, and switches back when you're home. You can also start it by hand with
the `travel` command above, or on the map with **📍 Travel → Use this
computer's location**; a hand-started trip lasts two days, or send `home`. It
shares adsb.lol's daily allowance, so home is checked a little less often
meanwhile.

### My flight

Follow a flight you or family are taking: on the map, **In the air → Your
flights → ＋ Follow a flight**, or send `flight UA1234`. Type the number from
the ticket (like *UA 1234* or *DL573*). You get a notification when it takes
off, if it flies over your house (*look up and wave!*), and when it lands,
and so does your family's feed. **Show** draws it on the map.

### Your family's feed

Family members can get the highlights (A380s and 747s, military planes,
emergencies, the space station, your flight taking off and landing), not
every plane. They install the free **ntfy** app, tap **+**, and subscribe to
the *family topic* from the phone setup email. `FAMILY_ALERTS=false` in `.env`
turns it off.

### Spotting with friends

If friends run planespotter too, you can compare collections. Your **friend
code** is in the phone setup email and on the map (*Collection → Friends*).
Swap codes, and add theirs to `.env`:

```
FRIENDS=Sam=planespotter-share-abc123def45678; Alex=planespotter-share-0123456789abcd
SHARE_NAME=Henry
```

*Collection → Friends* then shows who's spotted the most this week, their
totals and bingo, and your phone gets a ping when a friend spots something
rare (*🎉 Sam just spotted an Airbus A380*). Nothing about where anyone
lives is shared.

**ntfy's daily limit.** ntfy.sh's free service accepts 250 messages a day
from one internet connection (reading is free). Plane alerts always come
first: the widget status, family feed, friends and command replies only use
what's left, and stop for the day if fewer than 80 are left.

## Kiosk mode (an old tablet or a TV)

Open `http://192.168.1.23:8765/kiosk` (your map's phone address plus
`/kiosk`) on a tablet or a TV's browser: a full-screen map with a big clock,
the Look up! card and the next planes with countdowns. Nothing to tap.

- On an iPad, tap **Share → Add to Home Screen** so it opens full screen,
  and set **Settings → Display & Brightness → Auto-Lock** to **Never**
  while it's plugged in.
- On an Android tablet, add it to the home screen from Chrome's menu, and
  keep the screen on in the developer options or with a "stay awake" app.

## Look up for: the Moon, planets, contrails, the best time

- **🌕 Planes crossing the Moon.** When a plane is about to fly in front of the
  Moon as seen from your house, your phone gets a heads-up a few minutes
  before: *"DAL573 may fly right across the Moon at 6:42:15 pm. Look ESE,
  32° up."* The Look up! card counts down to it on the map. It's over in a
  split second, so start filming a few seconds early. The time is predicted
  from the plane's course, so it may turn. `TRANSIT_SUN=true` does the same
  for the Sun (**only ever look at or photograph the Sun through a proper
  solar filter**); `TRANSIT_ALERTS=false` turns them off.
- **Planets and bright stars** show in the sky view, labelled *(a planet,
  not a plane)*. After dark the map also says when the bright, steady light
  is Venus or Jupiter: they fool everyone.
- **Contrails:** the map and the morning email say whether planes will leave
  long-lasting white trails today (*Long-lasting contrails likely this
  afternoon*), from the forecast temperature and humidity at cruising height.
- **Best time to look up today:** the hour with the most planes you'd actually
  be able to see, from your history and the cloud forecast (*5–6 pm, usually
  about 9 planes, clear*). On the map and in the morning email.
- **Calendar:** space station passes (with a reminder 10 minutes before), the
  best time to look up each day, and your flights. The weekly email has the
  calendar file attached (tap it to add the events), or subscribe with
  **Collection → 📅 Add to my calendar** on your home Wi-Fi to keep it up to date.

## History: any day, and photos that match themselves

- **Time machine:** in **🕒 Replay**, choose **📅 Any day…** and pick a date to
  replay everything that flew over then (any day planespotter was watching).
- **Photos from a spotting session:** in **Collection → Your photos**, tap
  **📷 Add photos from a spotting session** and pick several at once. Each is
  matched to the plane that was over your zone when you took it, from the
  time the camera saved in the photo.

## Space station passes

planespotter works out when the International Space Station (and China's
Tiangong station and the Hubble telescope) fly over your house, using their
published orbits. A pass is **visible** when the station is lit by the sun
but your sky is dark, usually an hour or two after sunset or before
sunrise: it looks like a very bright, steady star moving fast, with no
blinking lights, and takes about 5 minutes to cross.

- The map lists the next visible passes (bottom of *In the air*), and the
  Look up! card points to the station while it's crossing.
- Your phone gets a notification 10 minutes before each visible pass that
  climbs at least 20° (not during quiet hours).
- The morning email lists tonight's passes.
- The sky view shows it too.

`SPACE_ALERTS=false` in `.env` turns it off.

## More places

Watch somewhere else as well, like work or a relative's house. Each place
gets its own alerts, daily and weekly emails, offline log, and map. Run
`python3 -m planespotter setup` and fill in question 8, or add a line to
`.env`:

```
PLACES=Work @ 47.6101,-122.3343 r2; Mum's house @ 51.5007,-0.1246
```

Each place is a name, `@`, its coordinates (from Google Maps, as in step 5),
and optionally `r` and a radius in miles (otherwise it uses yours). Separate
places with `;`. When planespotter starts it prints each place's map
address (the second place's is port 8767, the next 8768...), and a picker on
the map switches between them.

All places share adsb.lol's daily allowance, so each one is checked less
often: with a free key and two places, about every 4 minutes each.

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

Besides the plane alerts, your phone also gets:

- **🛫 Rush alerts** when 5 or more planes are about to come over in the next
  15 minutes (at most one an hour; not in quiet hours). Change the number
  with `RUSH_COUNT` in `.env`, or turn them off with `RUSH_ALERTS=false`.
- **🛬 Landed** for flights you follow.
- **🛰️ Space station** 10 minutes before a visible pass.
- **📴 Found while offline**: a summary after planespotter has been off.

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
| No voice | Turn on **🗣️ Voice**, then click anywhere on the map once (browsers only speak after a click). It's silent during quiet hours. On an iPhone, turn off silent mode. |
| *This Connection Is Not Private* on the sky view | Expected the first time: see *Sky view* above (Show Details → visit this website). |
| Sky view says *drag to look around* on a phone | It didn't get the compass. Open it from the **https** address, tap **Start** and allow motion and orientation. On an iPhone, if you tapped Don't Allow, close the tab and open the address again. |
| Point says *The compass wasn't allowed* | You tapped Don't Allow. Close that Safari tab, then tap 🔭 Point again and tap **Allow**. If it says *The compass isn't coming through*: Settings → Apps → Safari → turn on **Motion & Orientation Access** (if it's there), and Settings → Privacy & Security → Location Services → System Services → **Compass Calibration** on. |
| The secure address isn't printed | planespotter makes its certificate with `openssl`, which comes with macOS. If port 8766 is busy, set `MAP_HTTPS_PORT` in `.env`. |
| The noise meter's button is greyed out | Open the map at **http://localhost:8765** on the Mac running planespotter (not the 192.168… address). |
| *Can't open that kind of picture* when adding a photo | Some browsers can't read iPhone HEIC photos. Add it from Safari, or use a JPEG. |
| `phone` says *timed out* or *Couldn't email it* | The Wi-Fi (hotels, schools, offices) is blocking email. Run `python3 -m planespotter phone --copy widget` and paste on your iPhone (Universal Clipboard: same Apple ID, Bluetooth on), (Siri uses the same script). Or AirDrop the files in the `phone-setup` folder. |
| The widget says *Can't reach planespotter* | planespotter must be running on your Mac. Check the widget uses the script from `python3 -m planespotter phone` (older copies only work on home Wi-Fi). |
| Siri waits for you to tap **Done** before it answers | In Shortcuts, tap **⋯** on the shortcut, tap the arrow **›** on the **Run Script** step, and turn **Show When Run** off. |
| Siri just says *Done* or *OK* | Run `python3 -m planespotter phone --test` on the Mac. If it says nothing has been posted, planespotter isn't running (or the Mac is asleep): start it and wait 5 minutes. If it shows a sentence, the shortcut needs fixing: it should be just **Run Script** (Scriptable, script **planespotter**) and **Speak Text** with **Script Result**. |
| Siri keeps talking about a plane that has already gone by, or keeps saying one is overhead | Your shortcut reads the status link directly, so it repeats what your Mac last posted (and nothing new is posted while the Mac is asleep). If Siri starts with *"At 9:42 pm: …"* and ends with *"change your Siri shortcut…"*, that's it: rebuild the shortcut with Scriptable's **Run Script** (see *Siri* above), and update the planespotter script in Scriptable (`python3 -m planespotter phone --copy widget`, then paste over the old one). The script works out the answer when you ask. Something that stays in your zone (a helicopter hovering) is mentioned as *hovering nearby*, not as coming over. |
| Commands get no answer | Check you're on the *Commands page* from the phone setup email, and planespotter is running (`watch`). Answers come in the ntfy app within a minute. |
| *Not sending to ntfy: saving today's messages for plane alerts* in the log | ntfy's 250 a day are nearly used; the extras pause until tomorrow. Plane alerts carry on. |
| No Moon-crossing alerts | They need the Moon well up and bright, a plane heading right across it, phone notifications, and it not to be quiet hours. They're rare, so don't expect many. |
| *couldn't get the forecast / the weather / the orbit of...* in the log | Harmless: those extras (contrails, clouds, the space station) couldn't download, often on hotel or public Wi-Fi. planespotter keeps trying quietly in the background; plane alerts aren't affected. |
| The offline log found nothing | It only looks back about 20 hours, and can't find planes that have since landed far away. Check the weekly email for how much it covered. |
| Siri says it can't reach planespotter | planespotter must be running on your Mac and the Mac awake (`caffeinate -i python3 -m planespotter watch` keeps it awake). Run `python3 -m planespotter phone --test` to check the status is being posted. |
| The widget says *Can't get the planes* | The phone has no internet, or adsb.lol isn't answering: it tries again at the next refresh, and shows its last answer when it has one. |
| The widget shows **Home** while you're away (or the wrong town) | Scriptable needs your location: iPhone **Settings → Scriptable → Location → While Using the App or Widgets**, with **Precise Location** on. Without it, the widget uses where you last were, or home. |
| Alerts don't follow you when you travel | The planespotter script on your phone sends your location when the widget refreshes or you ask Siri: make sure it's the newest script (`python3 -m planespotter phone --copy widget`), that planespotter is running on your Mac, and that `FOLLOW_PHONE` isn't `off`. The Mac reacts within a minute or two. |
| `server` says *Connecting to the server didn't work* | Check the address (the server's **Public IP address**), that the user is `ubuntu`, and that you pasted your Mac's key when you made the server. Try `ssh ubuntu@<address>`: if that asks for a password or says *Permission denied*, the key is missing (in Oracle: the instance page → add the key, or make the server again). |
| The phone map says *Paused for today* | The phone has asked adsb.lol about 400 times today: it carries on tomorrow. The widget and Siri reuse recent answers meanwhile. |
| *planespotter runs on your server* when you start it on the Mac | It's running on your cloud server, so the Mac stays out of the way. `server status` checks on it; `server stop` brings it back to the Mac. |
| *planespotter is already running* | It's running by itself in the background (see *Always on*), so there's nothing to start. `python3 -m planespotter autostart status` shows what it's doing. |
| The widget always says *Nothing due right now* | Planes only show a few minutes before they come over, and iPhone refreshes widgets rarely. Update the widget script (`python3 -m planespotter phone --copy widget`, then paste it over the old one in Scriptable): the new one also shows your next regular flight, the last plane over and the last hour's count. |
| No runway line | It needs a big airport within about 60 miles and planes seen near it. The "planes an hour" part appears after a few days of watching. |
| No space station notifications | They need phone notifications set up, and only come for visible passes (dark sky, station in sunlight) that climb above 20°. Some weeks there are none. |
| No rush alerts | They need phone notifications set up, and 5 planes (`RUSH_COUNT`) due within 15 minutes. |
| No weather line | The nearest airport weather station may be far away or not reporting; it tries again every 10 minutes. `WEATHER=false` turns it off. |
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
| `python3 -m planespotter phone` | Emails you the phone setup: widget, Siri, commands, family topic, friend code. Add `--copy widget` (or `siri`, `commands`) to copy one part to the clipboard instead, or `--test` to see what Siri and the widget would say. |
| `python3 -m planespotter autostart` | Runs planespotter by itself (Mac: from login; Linux: from boot), all the time (`autostart status` to check on it, `autostart off` to stop). |
| `python3 -m planespotter server ubuntu@<address>` | Moves planespotter to an always-on cloud server (`server status`, `server update`, `server stop`). See *Run it in the cloud*. |
| `python3 -m planespotter gates aeroapi YOUR_KEY` | Gate numbers from FlightAware AeroAPI (or `aviationstack`); `gates test UAL1` checks it, `gates off` turns it off. |
| `python3 -m planespotter emails off` | No more emails: the reports come as phone notifications (`emails on` to undo). |

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
- **Quiet hours.** Planes are still recorded overnight, but nothing is
  sent to your phone: no plane alerts, special planes, rush, space station,
  Moon, My flight or offline-log notifications, and no news about following
  your phone. (Answers to commands you send, and test notifications, still
  come.) Nothing is saved up either: when quiet hours end, notifications
  start again with the next plane that flies through your zone. The morning
  report covers the 24 hours since the previous one, so night planes appear
  there, marked 🌙; if its time falls inside quiet hours, it waits until they
  end. The widget, Siri and the phone map aren't notifications: they work
  all night.
- **Routes are checked against the real flight.** Route databases map a
  flight number to a single leg, and are often wrong. So planespotter looks
  up the plane's actual flight as soon as it comes over (before the alert
  goes out, when the daily budget allows) and uses the airport nearest
  where it took off, and later where it landed or is about to. The
  airport list (about 12,000 airports, from the public-domain
  [OurAirports](https://ourairports.com/data/)) comes with planespotter;
  `python3 scripts/update_airports.py` refreshes it.
- **Clouds and daylight.** The weather comes from the latest report
  (METAR) of the airport weather station nearest your house, from the US
  National Weather Service's free [aviationweather.gov](https://aviationweather.gov)
  (it covers airports worldwide). It's checked at most every 15 minutes. If
  a plane is higher above the ground than a broken or overcast cloud layer,
  you won't see it. The sun's position is worked out on your Mac.
- **The noise meter** sends one number a second (the loudest moment of that
  second, in decibels). For each plane over your zone, planespotter finds
  when it was closest (from its path), takes the loudest second within 75
  seconds of that, and compares it with the quieter moments in the 10
  minutes around it.
- **The offline log** asks adsb.lol once for every plane within 250 nautical
  miles, picks the ones whose course leads back past your house (or that
  landed nearby, or that you see often), then looks up each one's track
  (`/1/trace/full`) and checks it against your zone.
- **The runway flow** comes from the direction of planes low (under 2,500 ft)
  and close (within 10 km) to your nearest big airport, from the live feed
  and from the whole flights of planes that crossed your zone. Each hour's
  flow is matched with how many planes came over you in that hour.
- **Satellite passes** use orbital elements from
  [CelesTrak](https://celestrak.org) (refreshed twice a day) and the
  standard SGP4 model, worked out on your Mac. A pass counts as visible when
  the satellite is in sunlight and the sun is more than 6° below your horizon.
- **The Moon, planets and stars** use Paul Schlyter's simplified orbits,
  checked against a full astronomy library to within a few arc-minutes, with
  the Moon corrected for being seen from your house. Moon crossings are
  found by flying each plane's course forward a second at a time.
- **Contrails and cloud forecasts** come from [Open-Meteo](https://open-meteo.com)
  (free, no key; your position is rounded to about 10 km).
- **The widget status, commands, family feed and friends** use extra private
  ntfy topics made from your main topic name. Anyone who knew your main
  topic could work them out, so keep it to yourself.
- **The secure address** uses a certificate planespotter makes once with
  `openssl` (kept in the `https-cert` folder; remade if your Mac's network
  address changes).
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

- A tiny e-ink or LED display by the window showing the next plane over.
- Smart lights (Philips Hue, Home Assistant) that flash when a plane is
  about to come over.
- Wind forecasts, to predict tomorrow's runway direction (and your sky).

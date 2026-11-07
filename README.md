# planespotter ✈

Get an email whenever a plane flies over your house, plus a daily sky
report every morning with yesterday's planes and the flights expected
overhead today.

- **Alerts:** callsign, airline, route (e.g. *Singapore (SIN) → Los Angeles (LAX)*),
  aircraft type, a photo, altitude, distance, and a link to follow it live.
- **Daily digest:** yesterday's planes with stats (busiest hour, lowest,
  closest, top airlines), and a forecast of today's regular flights with
  their usual times.
- Free data and no API keys. Pure Python standard library, so there's
  nothing to `pip install`.

## Quick start

You'll need Python 3.10 or newer.

```bash
cp .env.example .env
```

Open `.env` and set `HOME_LAT` / `HOME_LON` (right-click your house in
Google Maps and click the numbers to copy them) and `RADIUS_MILES`.

```bash
python3 -m planespotter check
```

`check` lists everything flying near you right now and marks what's inside
your zone. It's a good way to sanity-check the location and radius.

### Turn on email (Gmail)

1. Turn on [2-Step Verification](https://myaccount.google.com/security) for your Google account.
2. Create an app password at <https://myaccount.google.com/apppasswords>.
3. In `.env`, set `EMAIL_TO` and `SMTP_USER` to your Gmail address and
   `SMTP_PASSWORD` to the app password.

```bash
python3 -m planespotter test-email
```

Other providers work too: set `SMTP_HOST` and `SMTP_PORT` (587 for
STARTTLS, 465 for SSL).

Until email is set up, nothing is sent. Every email is saved as an `.html`
file in `outbox/` so you can open it in a browser and preview it.

### Run it

```bash
python3 -m planespotter watch
```

This polls every 20 seconds, emails an alert when a new plane enters the
zone, and sends the digest at `DIGEST_TIME`. It has to keep running to keep
watching, so run it on something that stays on.

**Raspberry Pi / Linux (systemd).** Create `/etc/systemd/system/planespotter.service`:

```ini
[Unit]
Description=planespotter
After=network-online.target
Wants=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/2-hour-project-
ExecStart=/usr/bin/python3 -m planespotter watch
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now planespotter
```

**Mac / Windows.** Leave it running in a terminal window. Or start it at
login: launchd on a Mac, Task Scheduler on Windows (on Windows also run
`pip install tzdata` if you set `TIMEZONE`).

## Commands

| Command | What it does |
| --- | --- |
| `check` | Lists planes near home right now. Sends and saves nothing. |
| `watch` | Runs forever: alerts plus the daily digest. |
| `digest` | Sends the digest right now. `--date YYYY-MM-DD` pretends it's another day. |
| `test-email` | Sends a test email. |

Add `--env path/to/.env` to use another settings file. All the settings are
explained in [`.env.example`](.env.example).

## How it works

- **Live positions** come from [adsb.lol](https://adsb.lol), a free
  community network of ADS-B receivers (data under ODbL).
  **Routes and aircraft details** come from [adsbdb.com](https://www.adsbdb.com).
  Lookups are cached in the local SQLite database.
- **Catching fast planes.** A jet can cross a 3-mile zone in under a
  minute, so the watcher looks a few miles past the edge of your zone. For
  each plane it checks the straight line between the last two positions,
  not just where the plane is now. A plane that crossed the zone between
  two polls still counts.
- **One email per pass.** Each plane gets one alert per trip through the
  zone. Seeing it again within `PASS_GAP_MINUTES` counts as the same pass.
  If several planes arrive at once, they share one email.
  `MAX_ALERTS_PER_HOUR` stops the alerts if you live under a busy approach
  path. Those planes still show up in the digest.
- **Wrong routes are dropped.** Route databases map a flight number to a
  single leg, but many flight numbers fly several legs a day. A route is
  only shown if the plane is actually somewhere along it.
- **Today's forecast is learned, not looked up.** Exact flight paths aren't
  published anywhere for free. Instead, the forecast uses the fact that
  airlines fly the same flight number at about the same time every day.
  Any flight that has passed over on at least 2 of the last 7 days is listed
  at its usual time. The forecast starts after a couple of days of watching
  and gets better over the first week.

## Limits

- Coverage depends on volunteer receivers near you. Most populated areas
  are well covered; run `check` to see yours.
- Some military and privacy-blocked aircraft don't show up.
- Gmail allows about 500 emails a day, which is far more than the hourly
  cap allows.

## Tests

```bash
python3 -m unittest discover -s tests
```

## Ideas for next time

- Quiet hours (no alerts overnight; those planes go in the digest).
- Push notifications (ntfy.sh, Telegram) alongside email.
- Only alert for interesting planes: helicopters, military aircraft,
  A380s, emergency squawks (7700).
- A small map in the digest showing the day's tracks.

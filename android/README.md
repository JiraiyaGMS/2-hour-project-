# planespotter for Android

The Android app: your planespotter's website in an app (any Android phone, whatever its browser), plus:

- a **home-screen widget**: the next plane over you with a countdown that ticks by itself, where to look,
  and the last one over (it reads your planespotter about every half hour, and whenever you open the app);
- **long-press shortcuts** on its icon: *What's that plane?* and *Where's my plane?* (read out loud with the
  phone's own voice), *Track a flight*, *Point*;
- the phone's **compass** for Point, and its **location** for "Use where I am now".

It asks for your planespotter's address once. Everything else comes from your planespotter, so its
nightly updates reach the app without a new app.

The ready-built app is `planespotter/web/planespotter.apk`, which your planespotter serves at
`/planespotter.apk` (account page → *Download the app*).

## Building it

Needs Java 17 or newer, Gradle 8.11 or newer, and the Android SDK (platform 35, build-tools 35):

```bash
cd android
echo "sdk.dir=/path/to/android-sdk" > local.properties
PLANESPOTTER_KEYSTORE=/path/to/planespotter.jks PLANESPOTTER_KEYSTORE_PASSWORD=... gradle assembleRelease
cp app/build/outputs/apk/release/app-release.apk ../planespotter/web/planespotter.apk
```

The signing key is never in this repository (anyone with it could make an "update" of the app). An
update must be signed with the same key as the installed app; one built with a different key won't install
over it, so uninstall the old one first (nothing is lost: everything lives on your planespotter, and you
just sign in again). Without `PLANESPOTTER_KEYSTORE` it's signed with your computer's debug key.

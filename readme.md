# Beakon
Beakon is designed to be a self-host location sharing webserver. Beakon aims to leak as little data as possible and uses mostly self-contained libraries and local database files. Where possible, it will reference local files and not reach out over any network. One area where this is not yet easy, is map tiling images.

![Screen Shot 2022-07-07 at 07 23 30](https://user-images.githubusercontent.com/4443705/177797804-51bdda0a-1f21-43f5-8ccd-7c2cae18cedf.png)


<img src="https://user-images.githubusercontent.com/4443705/177797819-fe324575-6c5c-4d1d-bad5-356e5855dbef.png" width="300"/>

## Setup

Run the setup script to get started:

```bash
./setup.sh
```

This will:
- Check prerequisites (Python 3.8+, sqlite3)
- Create a virtual environment and install dependencies
- Generate a SECRET_KEY
- Prompt for Mapbox API key (optional, maps won't work without it)
- Create config.cfg
- Initialize the database
- Optionally populate test data and install systemd service

After setup, start the server:

```bash
source .venv/bin/activate
python -c "import sys; sys.path.insert(0, 'src'); from app import app; app.run()"
```

Open https://localhost:5000 in your browser.

To disable registration after creating your account, edit `config.cfg` and set `REGISTRATION_ENABLED = False`.

## Deploying a fresh instance

```bash
git clone https://github.com/RealDudePerson/beakon.git
cd beakon
./setup.sh                                  # venv, deps, config, database
./scripts/fetch-companion-apk.sh            # download + verify the companion APK
source .venv/bin/activate
python -c "import sys; sys.path.insert(0, 'src'); from app import app; app.run()"
```

The companion APK is a 12.8 MB binary that is deliberately **not** committed to
git, so a fresh clone does not have it. `scripts/fetch-companion-apk.sh` downloads
the pinned build from the public GitHub release, verifies its size and sha256
against the tracked [`companion-apk.txt`](companion-apk.txt), and only then
installs it at `static/beakon-companion-debug.apk`. **Without this step the top
bar's "Download the Beakon app" menu item 404s.** No credentials are needed — the
release is public. The script is idempotent (it skips a destination that already
matches the pin) and never prints success unless the verified file is in place.

`setup.sh` runs this step for you as part of an interactive setup.

### Threat Model
This is completely built in my little spare time as a project to learn flask. This is my first flask project. This should not be used public facing, and if it is used public facing, disable registration once you have setup accounts.

Registered users are assumed to be good actors.

## Dependencies
### Python
flask

flask_sqlalchemy

flask_login

pyOpenSSL


### HTML/CSS/JS

[leaflet](https://github.com/Leaflet/Leaflet)

[foundation](https://github.com/foundation/foundation-sites)

[flatpickr](https://github.com/flatpickr/flatpickr)

### Hosted Dependencies
[mapbox]

### Useful Third Party Tools
[GPSLogger](https://gpslogger.app) is an open source android app that can automatically send requests to update your location in Beakon.

In GPSLogger you can use the 'Log to custom URL' feature to update your location in the background. The URL endpoing is /api/recordlocation.

The request should be sent as a json payload in the following format with the following headers:

Headers:

> Content-Type: application/json

> username: [username]

> secret: [API Key created from account page]

Body:

> {"lat":"%LAT","lon":"%LON","acc":"%ACC","batt":"%BATT"}


## Features

### Historic Location Viewing
Use the date picker above the map on the dashboard or map view to view locations from a specific day. The date picker allows you to select any date and see all recorded locations for that day as a connected path.

Points that are close together in both time and space are hidden to reduce clutter. A point is hidden if it is less than 5 minutes AND less than 50 meters from the previous point.

### Path Visualization
The map displays a color-coded path connecting your locations:
- Blue markers and lines indicate the most recent locations
- Green markers and lines indicate older locations
- Recent path segments are drawn as solid lines
- Older path segments (beyond the first few) are drawn as dashed lines
- Clicking any marker shows the exact timestamp in a popup

### Smart Auto-Refresh
The map automatically refreshes to check for new locations, but only recenters on new data. If you pan or zoom the map, your view is preserved when no new location data has arrived.

### Speedometer
A speedometer view is available at `/speed` for visualizing current travel speed.

### Android Companion App
The top bar has an overflow ("⋮") menu with a **Download the Beakon app** item that serves
Beakon's own companion app, `static/beakon-companion-debug.apk`, from Beakon itself (no
external host).

> **Maintenance:** the served file is a verified copy of the APK built from the
> `~/projects/beakon-android` project (package `com.beakon.companion`). The build
> to serve is pinned by [`companion-apk.txt`](companion-apk.txt) — tag, filename,
> size and sha256 — and downloaded by `scripts/fetch-companion-apk.sh`. To ship a
> new build: publish it as an asset on a new GitHub release, update
> `companion-apk.txt` to the new tag/size/sha256, then re-run
> `./scripts/fetch-companion-apk.sh`. Editing that one file is the only change
> needed; the tag and hash are never duplicated in code. The APK itself stays
> untracked in git — see `.gitignore` — so a **freshly cloned instance will not
> have it** until it runs the fetch step above.

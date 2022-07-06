# Beakon
Beakon is designed to be a self-host location sharing webserver. Beakon aims to leak as little data as possible and uses mostly self-contained libraries and local database files. Where possible, it will reference local files and not reach out over any network. One area where this is not yet easy, is map tiling images.

## Setup
Create a [mapbox] account and generate an access token. Place that in the app.cfg file.

Generate a random string for the SECRET_KEY in the app.cfg file.

Load the site.

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
[foundation]

[leaflet]

### Hosted Dependencies
[mapbox]

### Useful Third Party Tools
[GPSLogger] is an open source android app that can automatically send requests to update your location in Beakon.

In GPSLogger you can use the 'Log to custom URL' feature to update your location in the background. The URL endpoing is /api/recordlocation.

The request should be sent as a json payload in the following format with the following headers:

Headers:

> Content-Type: application/json

> username: [username]

> secret: [API Key created from account page]

Body:

> {"lat":"%LAT","lon":"%LON","acc":"%ACC"}


[mapbox]: https://www.mapbox.com/
[leaflet]: https://github.com/Leaflet/Leaflet
[foundation]: https://github.com/foundation/foundation-sites
[gpslogger]: https://gpslogger.app
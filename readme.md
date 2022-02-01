# Beakon
Beakon is designed to be a self-host location sharing webserver. Beakon aims to leak as little data as possible and uses mostly self-contained libraries and local database files. Where possible, it will reference local files and not reach out over any network. One area where this is not yet easy, is map tiling images.

## Dependencies
### Python
flask
flask_sqlalchemy
flask_login
pyOpenSSL

### HTML/CSS/JS
Foundation
Leaflet

### Hosted Dependencies
MapBox



[mapbox]: https://www.mapbox.com/
[leaflet]: https://github.com/Leaflet/Leaflet
[foundation]: https://github.com/foundation/foundation-sites

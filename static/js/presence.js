// Presence search page logic, moved out of presence.html so the page needs no
// inline <script> (CSP). Server data arrives via data-* attributes on
// #presence-data.
(function () {
    'use strict';

    var dataEl = document.getElementById('presence-data');
    var mapboxapi = dataEl ? (dataEl.dataset.mapboxapi || '') : '';

    var map = L.map('map').setView([0, 0], 2);
    if (mapboxapi) {
        L.tileLayer('https://api.mapbox.com/styles/v1/{id}/tiles/{z}/{x}/{y}?access_token=' + mapboxapi, {
            attribution: 'Map data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, Imagery &copy; <a href="https://www.mapbox.com/">Mapbox</a>',
            maxZoom: 18,
            id: 'mapbox/streets-v11',
            tileSize: 512,
            zoomOffset: -1,
            accessToken: mapboxapi
        }).addTo(map);
    } else {
        beakonMapUnavailable(map);
    }

    var searchMarker = null;
    var searchCircle = null;
    var resultsLayer = L.layerGroup().addTo(map);

    function redraw() {
        if (!searchMarker) return;
        var radius = parseInt(document.getElementById('presence-radius').value, 10) || 100;
        var pos = searchMarker.getLatLng();
        if (searchCircle) map.removeLayer(searchCircle);
        searchCircle = L.circle(pos, {radius: radius, color: 'blue', fillColor: '#3388ff', fillOpacity: 0.2}).addTo(map);
    }

    map.on('click', function (e) {
        if (searchMarker) map.removeLayer(searchMarker);
        searchMarker = L.marker(e.latlng).addTo(map);
        redraw();
    });

    document.getElementById('presence-radius').addEventListener('input', redraw);

    document.getElementById('presence-search-btn').addEventListener('click', function () {
        if (!searchMarker) { alert('Click the map to pick a search point.'); return; }
        resultsLayer.clearLayers();
        var resultsEl = document.getElementById('presence-results');
        resultsEl.textContent = '';
        var params = new URLSearchParams({
            user: document.getElementById('presence-user').value,
            lat: searchMarker.getLatLng().lat,
            lon: searchMarker.getLatLng().lng,
            radius: document.getElementById('presence-radius').value
        });
        fetch('/api/presence?' + params.toString())
            .then(function (r) {
                if (!r.ok) {
                    return r.text().then(function (body) {
                        throw new Error('Server returned HTTP ' + r.status + (body ? ' (' + body.substring(0, 120) + ')' : ''));
                    });
                }
                return r.json();
            })
            .then(function (data) {
                if (data.error) { alert(data.error); return; }
                var heading = document.createElement('h6');
                heading.textContent = data.days.length + ' day(s) with hits';
                var list = document.createElement('ul');
                data.days.forEach(function (d) {
                    var item = document.createElement('li');
                    var date = document.createElement('b');
                    date.textContent = d.date;
                    item.appendChild(date);
                    item.appendChild(document.createTextNode(
                        ': ' + d.hits + ' hits (' + d.first + ' \u2192 ' + d.last + ')'));
                    list.appendChild(item);
                });
                resultsEl.appendChild(heading);
                resultsEl.appendChild(list);
                data.days.forEach(function (d) {
                    var m = L.marker([d.last_lat, d.last_lon]).addTo(resultsLayer);
                    m.bindPopup('<b>' + d.date + '</b><br>' + d.hits + ' hits<br>' + d.first + ' &rarr; ' + d.last);
                });
                if (data.days.length === 0) { alert('No hits inside the circle in recent history.'); }
            })
            .catch(function (e) { alert('Error: ' + (e && e.message ? e.message : e)); });
    });
})();

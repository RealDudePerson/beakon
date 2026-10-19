// Group ("everyone who shares with you") map logic, kept in a static file so
// the template needs no inline <script> (CSP). Mirrors static/js/map.js: a
// 10-second poll and format_timestamp() strings shown as text. There is no
// staleness/dimming rule here, exactly as there is none in the single-person
// view. Data comes from GET /api/locations/shared.
(function () {
    'use strict';

    var dataEl = document.getElementById('shared-map-data');
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

    var peopleLayer = L.layerGroup().addTo(map);
    var hidden = {};        // username -> true while toggled off
    var currentPeople = [];
    var fitted = false;     // fit the view once, on the first response
    var REFRESH_INTERVAL_MS = 10000;
    var refreshInterval = null;

    function colorFor(index) {
        // Golden-angle hues keep adjacent people visually distinct.
        return 'hsl(' + Math.round((index * 137.508) % 360) + ', 80%, 45%)';
    }

    function render(people) {
        peopleLayer.clearLayers();
        var bounds = L.latLngBounds([]);
        people.forEach(function (person, index) {
            if (hidden[person.username]) {
                return;
            }
            var color = colorFor(index);
            var trail = person.trail || [];
            var latlngs = trail.map(function (point) {
                return [point.lat, point.lon];
            });
            if (latlngs.length > 1) {
                L.polyline(latlngs, {
                    color: color, weight: 3, opacity: 0.8
                }).addTo(peopleLayer);
            }
            latlngs.forEach(function (latlng) { bounds.extend(latlng); });
            if (person.latest) {
                var marker = L.circleMarker([person.latest.lat, person.latest.lon], {
                    radius: 8, color: 'white', weight: 2,
                    fillColor: color, fillOpacity: 1
                }).addTo(peopleLayer);

                // Build popup/tooltip nodes with textContent: display names are
                // user-controlled and must never be parsed as HTML.
                var popup = document.createElement('div');
                var name = document.createElement('b');
                name.textContent = person.display_name;
                popup.appendChild(name);
                popup.appendChild(document.createElement('br'));
                popup.appendChild(document.createTextNode(person.latest.timestamp));
                marker.bindPopup(popup);

                var tip = document.createElement('span');
                tip.textContent = person.display_name;
                marker.bindTooltip(tip, {permanent: true, direction: 'top'});

                bounds.extend([person.latest.lat, person.latest.lon]);
            }
        });
        if (!fitted && bounds.isValid()) {
            map.fitBounds(bounds, {padding: [30, 30]});
            fitted = true;
        }
    }

    function renderLegend(people) {
        var legend = document.getElementById('shared-legend');
        var empty = document.getElementById('shared-empty');
        if (!legend) {
            return;
        }
        legend.textContent = '';
        if (people.length === 0) {
            legend.classList.add('hide');
            if (empty) { empty.classList.remove('hide'); }
            return;
        }
        if (empty) { empty.classList.add('hide'); }
        legend.classList.remove('hide');

        people.forEach(function (person, index) {
            var row = document.createElement('div');
            row.className = 'shared-legend-entry';

            var label = document.createElement('label');
            label.className = 'toggle-switch';
            var checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = !hidden[person.username];
            checkbox.addEventListener('change', function () {
                if (checkbox.checked) {
                    delete hidden[person.username];
                } else {
                    hidden[person.username] = true;
                }
                render(currentPeople);
            });
            var slider = document.createElement('span');
            slider.className = 'toggle-slider';
            label.appendChild(checkbox);
            label.appendChild(slider);

            var swatch = document.createElement('span');
            swatch.className = 'shared-swatch';
            swatch.style.backgroundColor = colorFor(index);

            var link = document.createElement('a');
            link.href = '/map/' + encodeURIComponent(person.username);
            link.textContent = person.display_name;

            row.appendChild(label);
            row.appendChild(swatch);
            row.appendChild(link);
            legend.appendChild(row);
        });
    }

    function fetchShared() {
        return fetch('/api/locations/shared')
            .then(function (response) {
                if (!response.ok) {
                    return response.json().catch(function () { return {}; })
                        .then(function (body) {
                            throw new Error(body.error ||
                                ('Server returned HTTP ' + response.status));
                        });
                }
                return response.json();
            })
            .then(function (data) {
                // Rebuilt from scratch every poll: a person who is no longer
                // shared with disappears and is never cached across polls.
                currentPeople = data.people || [];
                render(currentPeople);
                renderLegend(currentPeople);
            })
            .catch(function (err) {
                var errorEl = document.getElementById('shared-map-error');
                if (errorEl) {
                    errorEl.textContent = 'Could not load shared locations: ' +
                        (err && err.message ? err.message : err);
                    errorEl.classList.remove('hide');
                }
                console.error('Shared locations fetch error:', err);
            });
    }

    function startRefresh() {
        if (refreshInterval) { return; }
        refreshInterval = setInterval(fetchShared, REFRESH_INTERVAL_MS);
    }

    function stopRefresh() {
        if (refreshInterval) {
            clearInterval(refreshInterval);
            refreshInterval = null;
        }
    }

    document.addEventListener('visibilitychange', function () {
        if (document.hidden) {
            stopRefresh();
        } else {
            startRefresh();
        }
    });

    fetchShared();
    startRefresh();
})();

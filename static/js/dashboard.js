// Dashboard page logic, moved out of dashboard.html so the page needs no inline
// <script> (CSP). Server data arrives via data-* attributes on #dashboard-data.
(function () {
    'use strict';

    // ------------------------------------------------------------------
    // Live tracking (present whether or not the page has location data)
    // ------------------------------------------------------------------
    var trackingWatchId = null;
    var lastTrackingUpdateTime = 0;
    var trackingToggle = document.getElementById('tracking-toggle');
    var trackingStatus = document.getElementById('tracking-status');
    var trackingError = document.getElementById('tracking-error');

    function setTrackingUi(on) {
        trackingStatus.textContent = on ? 'Tracking On' : 'Tracking Off';
        trackingStatus.classList.toggle('off', !on);
        trackingStatus.classList.toggle('on', on);
    }

    function startTracking() {
        if (trackingWatchId !== null) return;
        trackingWatchId = navigator.geolocation.watchPosition(
            function (position) {
                var now = Date.now();
                if (now - lastTrackingUpdateTime < 10000) {
                    return; // Ignore update
                }
                lastTrackingUpdateTime = now;

                var data = {
                    lat: position.coords.latitude,
                    lon: position.coords.longitude,
                    acc: position.coords.accuracy
                };
                // Poll battery on every tick
                if (navigator.getBattery) {
                    navigator.getBattery().then(function (battery) {
                        data.batt = Math.round(battery.level * 100);
                        data.ischarging = battery.charging;
                        doTrackingPost(data);
                    }).catch(function () {
                        doTrackingPost(data);
                    });
                } else {
                    doTrackingPost(data);
                }
            },
            function (error) {
                trackingError.textContent = 'Location error: ' + error.message;
                trackingError.classList.remove('hide');
                stopTracking();
                trackingToggle.checked = false;
                setTrackingUi(false);
            },
            { enableHighAccuracy: true }
        );
    }

    function doTrackingPost(data) {
        fetch('/recordlocation', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data)
        });
    }

    function stopTracking() {
        if (trackingWatchId !== null) {
            navigator.geolocation.clearWatch(trackingWatchId);
            trackingWatchId = null;
        }
    }

    // Restore tracking state from sessionStorage
    if (sessionStorage.getItem('trackingEnabled') === 'true') {
        trackingToggle.checked = true;
        startTracking();
        setTrackingUi(true);
    }

    trackingToggle.addEventListener('change', function () {
        if (this.checked) {
            startTracking();
            setTrackingUi(true);
            sessionStorage.setItem('trackingEnabled', 'true');
        } else {
            stopTracking();
            setTrackingUi(false);
            sessionStorage.setItem('trackingEnabled', 'false');
        }
    });

    // Stop tracking on page unload
    window.addEventListener('beforeunload', stopTracking);

    // ------------------------------------------------------------------
    // Map + auto-refresh (only when the page has location data)
    // ------------------------------------------------------------------
    var dataEl = document.getElementById('dashboard-data');
    if (!dataEl) {
        return;
    }

    var initialLat = parseFloat(dataEl.dataset.lat);
    var initialLon = parseFloat(dataEl.dataset.lon);
    var mapboxapi = dataEl.dataset.mapboxapi || '';
    var knownPlaces = JSON.parse(dataEl.dataset.knownPlaces || '[]');
    var initialLocations = JSON.parse(dataEl.dataset.locations || '[]');

    var map = L.map('map').setView([initialLat, initialLon], 13);
    if (mapboxapi) {
        L.tileLayer('https://api.mapbox.com/styles/v1/{id}/tiles/{z}/{x}/{y}?access_token=' + mapboxapi, {
            attribution: 'Map data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, Imagery © <a href="https://www.mapbox.com/">Mapbox</a>',
            maxZoom: 18,
            id: 'mapbox/streets-v11',
            tileSize: 512,
            zoomOffset: -1,
            accessToken: mapboxapi
        }).addTo(map);
    } else {
        beakonMapUnavailable(map);
    }

    var markersLayer = L.layerGroup().addTo(map);
    var locationsLayer = L.layerGroup().addTo(map);
    var geofencesLayer = L.layerGroup().addTo(map);

    function renderGeofences() {
        geofencesLayer.clearLayers();
        var isChecked = document.getElementById('geofence-toggle').checked;
        document.getElementById('geofence-status').textContent = isChecked ? 'Geofence Visibility: On' : 'Geofence Visibility: Off';
        document.getElementById('geofence-status').className = 'tracking-status ' + (isChecked ? 'on' : 'off');

        if (isChecked) {
            knownPlaces.forEach(function (place) {
                if (place.enabled) {
                    L.circle([place.lat, place.lon], {
                        radius: place.radius,
                        color: 'blue',
                        fillColor: '#3388ff',
                        fillOpacity: 0.2
                    }).addTo(geofencesLayer).bindPopup(place.name);
                }
            });
        }
    }
    renderGeofences();
    document.getElementById('geofence-toggle').addEventListener('change', renderGeofences);

    flatpickr('#date-picker', {
        defaultDate: null,
        allowInput: true,
        altInput: true,
        altFormat: 'F j, Y',
        dateFormat: 'Y-m-d',
        maxDate: 'today'
    });

    function haversineMeters(lat1, lon1, lat2, lon2) {
        var R = 6371000;
        var phi1 = lat1 * Math.PI / 180;
        var phi2 = lat2 * Math.PI / 180;
        var deltaPhi = (lat2 - lat1) * Math.PI / 180;
        var deltaLambda = (lon2 - lon1) * Math.PI / 180;
        var a = Math.sin(deltaPhi / 2) * Math.sin(deltaPhi / 2) +
                Math.cos(phi1) * Math.cos(phi2) *
                Math.sin(deltaLambda / 2) * Math.sin(deltaLambda / 2);
        var c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
        return R * c;
    }

    function clusterLocations(locations) {
        if (locations.length === 0) return [];
        var clustered = [locations[0]];
        for (var i = 1; i < locations.length; i++) {
            var curr = locations[i];
            var prev = clustered[clustered.length - 1];
            var dist = haversineMeters(prev.lat, prev.lon, curr.lat, curr.lon);
            var timeDiff = (new Date(curr.timestamp) - new Date(prev.timestamp)) / 1000 / 60;
            if (dist >= 50 || timeDiff >= 5) {
                clustered.push(curr);
            }
        }
        return clustered;
    }

    function createIcon(idx, total) {
        var isNewest = idx === 0;
        var size = isNewest ? 16 : 12 - (idx * 0.8);
        var hue = 210 - (idx * 15);
        return L.divIcon({
            className: 'custom-marker',
            html: '<div style="width:' + size + 'px;height:' + size + 'px;background:hsl(' + hue + ', 80%, 50%);border:2px solid white;border-radius:50%;box-shadow:0 2px 4px rgba(0,0,0,0.3);"></div>',
            iconSize: [size, size],
            iconAnchor: [size / 2, size / 2]
        });
    }

    function updateMap(locations, isNewData) {
        markersLayer.clearLayers();
        locationsLayer.clearLayers();
        var filtered = clusterLocations(locations);
        for (var i = 0; i < filtered.length - 1; i++) {
            var hue = 210 - (i * 15);
            var isDashed = i >= 3;
            var segment = L.polyline([
                [filtered[i].lat, filtered[i].lon],
                [filtered[i + 1].lat, filtered[i + 1].lon]
            ], {
                color: 'hsl(' + hue + ', 80%, 50%)',
                weight: 3,
                opacity: 0.8,
                dashArray: isDashed ? '5, 5' : null
            });
            locationsLayer.addLayer(segment);
        }
        filtered.forEach(function (loc, idx) {
            var isNewest = idx === filtered.length - 1;
            var marker = L.marker([loc.lat, loc.lon], {icon: createIcon(idx, filtered.length)});
            marker.bindPopup('<b>' + (isNewest ? 'Current Location' : 'Location ' + (filtered.length - idx)) + '</b><br>' + loc.timestamp);
            markersLayer.addLayer(marker);
        });
        if (filtered.length > 0 && !selectedDate && isNewData) {
            map.panTo([filtered[0].lat, filtered[0].lon], {animate: true, duration: 1});
        }
    }

    function updateStats(latest) {
        if (!latest) return;
        document.getElementById('stat-timestamp').textContent = latest.timestamp;
        document.getElementById('stat-lat').textContent = latest.lat;
        document.getElementById('stat-lon').textContent = latest.lon;
        document.getElementById('stat-gmap').href = 'https://maps.google.com/?q=' + latest.lat + ',' + latest.lon;

        var battLi = document.getElementById('stat-batt-li');
        var battSpan = document.getElementById('stat-batt');
        var chargingSpan = document.getElementById('stat-charging');

        if (latest.batt !== null && latest.batt !== undefined) {
            if (battSpan) battSpan.textContent = latest.batt;
            if (chargingSpan) chargingSpan.textContent = latest.ischarging ? '⚡' : '';
            if (battLi) battLi.classList.remove('hide');
        } else if (battLi) {
            battLi.classList.add('hide');
        }
    }

    updateMap(initialLocations, true);

    var refreshInterval = null;
    var REFRESH_INTERVAL_MS = 10000;
    var selectedDate = null;
    var lastKnownTimestamp = null;

    function fetchLocations() {
        var url = '/api/locations';
        if (selectedDate) {
            url += '?date=' + selectedDate;
        }
        return fetch(url)
            .then(function (response) { return response.json(); })
            .then(function (data) {
                if (data.locations && data.locations.length > 0) {
                    var isNewData = lastKnownTimestamp === null || data.latest.timestamp !== lastKnownTimestamp;
                    updateMap(data.locations, isNewData);
                    if (isNewData) {
                        updateStats(data.latest);
                    }
                    lastKnownTimestamp = data.latest.timestamp;
                }
            })
            .catch(function (err) { console.error('Fetch error:', err); });
    }

    document.getElementById('date-picker').addEventListener('change', function (e) {
        selectedDate = e.target.value;
        fetchLocations();
    });

    function startRefresh() {
        if (refreshInterval) return;
        refreshInterval = setInterval(function () {
            fetchLocations();
        }, REFRESH_INTERVAL_MS);
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

    startRefresh();
})();

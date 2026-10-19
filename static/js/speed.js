// Speedometer page logic, moved out of speed.html so the page needs no inline
// <script> (CSP).
(function () {
    'use strict';

    var currentSpeed = 0;
    var maxSpeed = 0;
    var totalDistance = 0;
    var lastPosition = null;
    var lastTimestamp = null;
    var watchId = null;
    var trackingActive = false;

    var needle = document.getElementById('needle');
    var currentSpeedEl = document.getElementById('current-speed');
    var maxSpeedEl = document.getElementById('max-speed');
    var directionEl = document.getElementById('direction');
    var distanceEl = document.getElementById('distance');
    var statusEl = document.getElementById('status-message');

    var MAX_MPH = 40;

    function toRadians(deg) {
        return deg * Math.PI / 180;
    }

    function haversineDistance(lat1, lon1, lat2, lon2) {
        var R = 3958.8;
        var dLat = toRadians(lat2 - lat1);
        var dLon = toRadians(lon2 - lon1);
        var a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
                Math.cos(toRadians(lat1)) * Math.cos(toRadians(lat2)) *
                Math.sin(dLon / 2) * Math.sin(dLon / 2);
        var c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
        return R * c;
    }

    function getCompassDirection(heading) {
        if (heading === null || heading === undefined) return '--';
        var directions = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
        var idx = Math.round(heading / 22.5) % 16;
        return directions[idx];
    }

    function updateNeedle(speedMph) {
        var angle = -90 + (speedMph / MAX_MPH) * 180;
        angle = Math.max(-90, Math.min(90, angle));
        needle.setAttribute('transform', 'rotate(' + angle + ', 100, 100)');
    }

    function updateDisplay(speedMph, heading, distanceMi) {
        currentSpeedEl.textContent = Math.round(speedMph);
        maxSpeedEl.textContent = Math.round(speedMph) + ' MPH';
        directionEl.textContent = getCompassDirection(heading);
        distanceEl.textContent = distanceMi.toFixed(2) + ' mi';
        updateNeedle(speedMph);
    }

    function handlePosition(position) {
        var speedMph = 0;
        var heading = position.coords.heading;
        var currentLat = position.coords.latitude;
        var currentLon = position.coords.longitude;
        var currentTimestamp = position.timestamp;

        if (lastPosition !== null && lastTimestamp !== null) {
            var dist = haversineDistance(
                lastPosition.latitude, lastPosition.longitude,
                currentLat, currentLon
            );
            totalDistance += dist;

            var timeDeltaMs = currentTimestamp - lastTimestamp;
            if (timeDeltaMs > 0) {
                var timeDeltaHours = timeDeltaMs / (1000 * 60 * 60);
                speedMph = dist / timeDeltaHours;
            }
        }

        lastPosition = {
            latitude: currentLat,
            longitude: currentLon
        };
        lastTimestamp = currentTimestamp;

        if (speedMph > maxSpeed) {
            maxSpeed = speedMph;
        }

        updateDisplay(speedMph, heading, totalDistance);

        if (!trackingActive) {
            trackingActive = true;
            statusEl.textContent = 'Tracking active';
            statusEl.classList.add('active');
        }
    }

    function handleError(error) {
        var messages = {
            1: 'Location permission denied',
            2: 'Location unavailable',
            3: 'Location request timeout',
            0: 'Location error'
        };
        statusEl.textContent = messages[error.code] || 'GPS error';
        statusEl.classList.add('error');
    }

    function startTracking() {
        if (watchId !== null) return;

        if ('geolocation' in navigator) {
            statusEl.textContent = 'Acquiring GPS...';

            watchId = navigator.geolocation.watchPosition(
                handlePosition,
                handleError,
                {
                    enableHighAccuracy: true,
                    maximumAge: 1000,
                    timeout: 15000
                }
            );
        } else {
            statusEl.textContent = 'Geolocation not supported';
            statusEl.classList.add('error');
        }
    }

    startTracking();
})();

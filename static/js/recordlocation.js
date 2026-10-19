// Browser location recorder, moved out of recordlocation.html so the page needs
// no inline <script> (CSP).
let lastSent = 0;

function locationSuccess(position) {
    const now = Date.now();
    if (now - lastSent < 10000) return; // throttle: one POST per 10s
    lastSent = now;
    const latitude = position.coords.latitude;
    const longitude = position.coords.longitude;
    const accuracy = position.coords.accuracy;
    const data = {
        lat: latitude,
        lon: longitude,
        acc: accuracy
    };
    fetch('/recordlocation', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
    }).then(res => {
        console.log('Request complete! response:', res);
    });
}

function locationError(error) {
    console.log('Location error:', error.code, error.message);
}

navigator.geolocation.watchPosition(locationSuccess, locationError);

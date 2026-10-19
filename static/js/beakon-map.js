// Shared Leaflet helper, loaded by layout.html on every page. Kept in a static
// file so templates carry no inline <script> (CSP: no 'unsafe-inline').
//
// Graceful degradation when no Mapbox access token is configured. The Leaflet
// map object is still created so page scripts can add markers, but no tile
// request with an empty access_token is made.
function beakonMapUnavailable(map) {
    var notice = document.createElement('div');
    notice.className = 'callout warning';
    notice.style.cssText = 'position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);z-index:1000;margin:0;text-align:center;max-width:80%;';
    notice.textContent = 'Map unavailable: no Mapbox API key configured.';
    map.getContainer().appendChild(notice);
}

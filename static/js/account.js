var nameUpdateButton = document.querySelector('#name-update-btn');
var addAccessButton = document.querySelector('#add-access-btn');
var removeAccessButton = document.querySelector('#remove-access-btn');
var updatePasswordButton = document.querySelector('#update-password-btn');
var deleteLocationButton = document.querySelector('#delete-location-btn');

nameUpdateButton.addEventListener('click', function(){
    console.log("NameUpdateButton clicked.");
    let fname = document.getElementById("fname").value;
    let lname = document.getElementById("lname").value;
    let data = {
        fname: fname,
        lname: lname
    };
    fetch("/account/update_name", {
        method: "POST",
        headers: {'Content-Type': 'application/json'}, 
        body: JSON.stringify(data)
    }).then(res => {
        console.log("Request complete! response:", res); 
        if(res.status == 201){
            if(fname){
                document.getElementById('first-name-span').textContent = fname;
            }
            if(lname){
                document.getElementById('last-name-span').textContent = lname;
            }
        }
    });
});

addAccessButton.addEventListener('click', function(){
    console.log("addAccessButton clicked.");
    let grantUserName = document.getElementsByName('add_permission_username')[0].value;
    let data = {
        username: grantUserName
    };
    fetch("/account/add_permission", {
        method: "POST",
        headers: {'Content-Type': 'application/json'}, 
        body: JSON.stringify(data)
    }).then(res => {
        console.log("Request complete! response:", res); 
        if(res.status == 201){
            var ul = document.getElementById('shared-with-list');
            var li = document.createElement('li');
            li.appendChild(document.createTextNode(grantUserName));
            var liId = "li-" + grantUserName;
            li.setAttribute('id',liId);
            ul.appendChild(li);
        }
    });
});

removeAccessButton.addEventListener('click', function(){
    console.log("removeAccessButton clicked.");
    let revokeUserName = document.getElementsByName('remove_permission_username')[0].value;
    let data = {
        username: revokeUserName
    };
    fetch("/account/remove_permission", {
        method: "POST",
        headers: {'Content-Type': 'application/json'}, 
        body: JSON.stringify(data)
    }).then(res => {
        console.log("Request complete! response:", res); 
        if(res.status == 201){
            var username = "li-"+revokeUserName;
            var li = document.getElementById(username);
            li.parentNode.removeChild(li);
        }
    });
});


updatePasswordButton.addEventListener('click', function(){
    console.log("Password Update Button clicked.");
    let passwordUpdateError = document.getElementById("password_update_error");
    let currentPassword = document.getElementById("current_password").value;
    let newPassword = document.getElementById("new_password").value;
    let newPasswordRepeat = document.getElementById("new_password_repeat").value;
    if(newPassword==newPasswordRepeat){
        passwordUpdateError.classList.add('hide');
        let data = {
            current_password: currentPassword,
            new_password: newPassword
        };
        fetch("/account/update_password", {
            method: "POST",
            headers: {'Content-Type': 'application/json'}, 
            body: JSON.stringify(data)
        }).then(res => {
            console.log("Request complete! response:", res); 
            if(res.status == 201){
                console.log("Password updated successfully.");
                passwordUpdateError.textContent = "Password udpated successfully.";
                passwordUpdateError.classList.remove('alert');
                passwordUpdateError.classList.add('success');
                passwordUpdateError.classList.remove('hide');
            }else if(res.status == 401){
                console.log("Password update failed.");
                passwordUpdateError.textContent = "Current password incorrect.";
                passwordUpdateError.classList.add('alert');
                passwordUpdateError.classList.remove('hide');
                passwordUpdateError.classList.remove('success');
            }
        });
    }else{
        console.log("New passwords do not match.");
        passwordUpdateError.textContent = "Passwords do not match.";
        passwordUpdateError.classList.add('alert');
        passwordUpdateError.classList.remove('success');
        passwordUpdateError.classList.remove('hide');
    }
});

deleteLocationButton.addEventListener('click', function(){
    console.log("Delete location Button clicked.");
    let deleteLocationError = document.getElementById("delete-location-error");
    let deleteInput = document.getElementById("delete-input").value;
    if(deleteInput.toLowerCase()=='delete'){
        deleteLocationError.classList.add('hide');
        let data = {
            delete: true
        };
        fetch("/account/delete_locations", {
            method: "POST",
            headers: {'Content-Type': 'application/json'}, 
            body: JSON.stringify(data)
        }).then(res => {
            console.log("Request complete! response:", res); 
            if(res.status == 201){
                console.log("Location data deleted.");
                deleteLocationError.textContent = "Location data deleted.";
                deleteLocationError.classList.remove('alert');
                deleteLocationError.classList.add('success');
                deleteLocationError.classList.remove('hide');
            }
        });
    }else{
        console.log("Type 'delete' in the text box if you want to remove location data.");
        deleteLocationError.textContent = "Type 'delete' in the text box if you want to remove location data.";
        deleteLocationError.classList.add('alert');
        deleteLocationError.classList.remove('success');
        deleteLocationError.classList.remove('hide');
    }
});

// Known Places logic
var editingPlaceId = null;
var modalMode = 'add'; // 'add', 'edit', 'view'

function togglePlace(id, currentEnabled) {
    fetch('/api/places', {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: id, enabled: !currentEnabled})
    }).then(function() { loadPlaces(); });
}

function viewPlace(id, lat, lon, name) {
    modalMode = 'view';
    editingPlaceId = null;
    document.getElementById('place-modal-title').textContent = 'View: ' + name;
    document.getElementById('place-name').value = '';
    document.getElementById('place-radius').value = '';
    document.getElementById('place-webhook').value = '';
    document.getElementById('place-include-coords').checked = false;
    document.getElementById('place-enabled').checked = false;
    // Hide form fields and save button in view mode
    document.getElementById('place-name').parentElement.style.display = 'none';
    document.getElementById('place-radius').parentElement.style.display = 'none';
    document.getElementById('place-webhook').parentElement.style.display = 'none';
    document.getElementById('place-include-coords').parentElement.style.display = 'none';
    document.getElementById('place-enabled').parentElement.style.display = 'none';
    document.getElementById('save-place-btn').style.display = 'none';
    // Store coords for map centering
    window._pendingPlaceView = {lat: lat, lon: lon};
    $('#place-modal').foundation('open');
}

function editPlace(id, name, lat, lon, radius, webhook_url, enabled, include_coords_in_webhook) {
    modalMode = 'edit';
    editingPlaceId = id;
    document.getElementById('place-modal-title').textContent = 'Edit Known Place';
    // Show form fields and save button
    document.getElementById('place-name').parentElement.style.display = '';
    document.getElementById('place-radius').parentElement.style.display = '';
    document.getElementById('place-webhook').parentElement.style.display = '';
    document.getElementById('place-include-coords').parentElement.style.display = '';
    document.getElementById('place-enabled').parentElement.style.display = '';
    document.getElementById('save-place-btn').style.display = '';
    // Pre-populate fields
    document.getElementById('place-name').value = name;
    document.getElementById('place-radius').value = radius;
    document.getElementById('place-webhook').value = webhook_url;
    document.getElementById('place-include-coords').checked = include_coords_in_webhook;
    document.getElementById('place-enabled').checked = enabled;
    // Store coords for map centering
    window._pendingPlaceView = {lat: lat, lon: lon};
    $('#place-modal').foundation('open');
}

function openAddPlaceModal() {
    modalMode = 'add';
    editingPlaceId = null;
    document.getElementById('place-modal-title').textContent = 'Add Known Place';
    // Show form fields and save button
    document.getElementById('place-name').parentElement.style.display = '';
    document.getElementById('place-radius').parentElement.style.display = '';
    document.getElementById('place-webhook').parentElement.style.display = '';
    document.getElementById('place-include-coords').parentElement.style.display = '';
    document.getElementById('place-enabled').parentElement.style.display = '';
    document.getElementById('save-place-btn').style.display = '';
    // Clear fields
    document.getElementById('place-name').value = '';
    document.getElementById('place-radius').value = '100';
    document.getElementById('place-webhook').value = '';
    document.getElementById('place-include-coords').checked = false;
    document.getElementById('place-enabled').checked = true;
    window._pendingPlaceView = null;
    $('#place-modal').foundation('open');
}

function testPlace(id) {
    fetch('/api/places/' + id + '/test', {
        method: 'POST'
    })
    .then(function(res) { return res.json(); })
    .then(function(data) {
        alert('Webhook Test Result: ' + (data.status || data.error));
    })
    .catch(function(err) {
        alert('Test failed: ' + err);
    });
}

function deletePlace(id) {
    fetch('/api/places', {
        method: 'DELETE',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: id})
    }).then(function() { loadPlaces(); });
}

// Build a text node so a place field is never parsed as HTML (S4).
function textElement(tag, text) {
    var node = document.createElement(tag);
    if (text !== undefined && text !== null) {
        node.textContent = String(text);
    }
    return node;
}

function placeButton(label, className, handler) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'button small ' + className;
    button.textContent = label;
    button.addEventListener('click', handler);
    return button;
}

// Render the known-places table from /api/places. Every value that originates
// from user input (name, webhook_url, coordinates, radius) is inserted with
// textContent / DOM properties -- never string-concatenated markup -- so a
// place named "<img src=x onerror=...>" is shown as text and cannot execute
// (S4).
function loadPlaces() {
    fetch('/api/places')
        .then(function(res) { return res.json(); })
        .then(function(data) {
            var tbody = document.querySelector('#places-table tbody');
            while (tbody.firstChild) {
                tbody.removeChild(tbody.firstChild);
            }
            data.places.forEach(function(place) {
                var truncWebhook = place.webhook_url.length > 30 ? place.webhook_url.substring(0, 30) + '...' : place.webhook_url;
                var enabledBtnClass = place.enabled ? 'success' : 'secondary';
                var enabledBtnText = place.enabled ? 'On' : 'Off';

                var row = document.createElement('tr');

                var nameTd = textElement('td', place.name);
                nameTd.setAttribute('data-label', 'Name');
                row.appendChild(nameTd);

                var coordTd = textElement('td', place.lat.toFixed(5) + ', ' + place.lon.toFixed(5));
                coordTd.setAttribute('data-label', 'Coordinates');
                coordTd.className = 'show-for-medium';
                row.appendChild(coordTd);

                var radiusTd = textElement('td', place.radius);
                radiusTd.setAttribute('data-label', 'Radius');
                row.appendChild(radiusTd);

                var webhookTd = textElement('td', truncWebhook);
                webhookTd.setAttribute('data-label', 'Webhook');
                webhookTd.className = 'show-for-medium';
                webhookTd.title = place.webhook_url;
                row.appendChild(webhookTd);

                var enabledTd = document.createElement('td');
                enabledTd.setAttribute('data-label', 'Enabled');
                enabledTd.appendChild(placeButton(enabledBtnText, enabledBtnClass, function() {
                    togglePlace(place.id, place.enabled);
                }));
                row.appendChild(enabledTd);

                var actionsTd = document.createElement('td');
                actionsTd.setAttribute('data-label', 'Actions');
                actionsTd.appendChild(placeButton('View', '', function() {
                    viewPlace(place.id, place.lat, place.lon, place.name);
                }));
                actionsTd.appendChild(document.createTextNode(' '));
                actionsTd.appendChild(placeButton('Edit', 'warning', function() {
                    editPlace(place.id, place.name, place.lat, place.lon, place.radius,
                              place.webhook_url, place.enabled,
                              place.include_coords_in_webhook);
                }));
                actionsTd.appendChild(document.createTextNode(' '));
                actionsTd.appendChild(placeButton('Test', 'secondary', function() {
                    testPlace(place.id);
                }));
                actionsTd.appendChild(document.createTextNode(' '));
                actionsTd.appendChild(placeButton('Delete', 'alert', function() {
                    deletePlace(place.id);
                }));
                row.appendChild(actionsTd);

                tbody.appendChild(row);
            });
        });
}

// ---------------------------------------------------------------------------
// Place modal map (moved out of account.html; CSP has no 'unsafe-inline').
// The Mapbox token arrives via a data-* attribute on #place-map.
// ---------------------------------------------------------------------------
var mapboxToken = '';
var placeMap;
var marker;
var mapInitialized = false;

function initPlaceMap() {
    if (mapInitialized) {
        return;
    }
    var mapEl = document.getElementById('place-map');
    mapboxToken = mapEl.dataset.mapboxapi || '';
    placeMap = L.map('place-map').setView([0, 0], 2);
    if (mapboxToken) {
        L.tileLayer('https://api.mapbox.com/styles/v1/{id}/tiles/{z}/{x}/{y}?access_token=' + mapboxToken, {
            id: 'mapbox/streets-v11',
            accessToken: mapboxToken
        }).addTo(placeMap);
    } else {
        beakonMapUnavailable(placeMap);
    }

    placeMap.on('click', function(e) {
        if (modalMode === 'view') return;
        if (marker) placeMap.removeLayer(marker);
        marker = L.marker(e.latlng).addTo(placeMap);
    });

    document.getElementById('save-place-btn').addEventListener('click', function() {
        if (!marker) return;
        var payload = {
            name: document.getElementById('place-name').value,
            lat: marker.getLatLng().lat,
            lon: marker.getLatLng().lng,
            radius: parseInt(document.getElementById('place-radius').value, 10) || 100,
            webhook_url: document.getElementById('place-webhook').value,
            enabled: document.getElementById('place-enabled').checked,
            include_coords_in_webhook: document.getElementById('place-include-coords').checked
        };

        if (modalMode === 'edit' && editingPlaceId) {
            payload.id = editingPlaceId;
            fetch('/api/places', {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            }).then(function() {
                $('#place-modal').foundation('close');
                loadPlaces();
            });
        } else {
            fetch('/api/places', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            }).then(function() {
                $('#place-modal').foundation('close');
                loadPlaces();
            });
        }
    });

    mapInitialized = true;
}

$(document).on('open.zf.reveal', '#place-modal', function() {
    initPlaceMap();
    setTimeout(function() {
        placeMap.invalidateSize();
        if (marker) {
            placeMap.removeLayer(marker);
            marker = null;
        }
        if (window._pendingPlaceView) {
            var coords = window._pendingPlaceView;
            placeMap.setView([coords.lat, coords.lon], 15);
            marker = L.marker([coords.lat, coords.lon]).addTo(placeMap);
            window._pendingPlaceView = null;
        } else {
            placeMap.setView([0, 0], 2);
        }
    }, 200);
});

$(document).on('closed.zf.reveal', '#place-modal', function() {
    editingPlaceId = null;
    modalMode = 'add';
});

document.addEventListener('DOMContentLoaded', function() {
    loadPlaces();
    document.getElementById('add-place-btn').addEventListener('click', openAddPlaceModal);
});

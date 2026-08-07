var nameUpdateButton = document.querySelector('#name-update-btn');
var addAccessButton = document.querySelector('#add-access-btn');
var removeAccessButton = document.querySelector('#remove-access-btn');
var updatePasswordButton = document.querySelector('#update-password-btn');
var deleteLocationButton = document.querySelector('#delete-location-btn');

nameUpdateButton.addEventListener('click', function(){
    console.log("NameUpdateButton clicked.");
    let fname = document.getElementById("fname").value;
    console.log(fname)
    let lname = document.getElementById("lname").value;
    console.log(lname)
    let data = {
        fname: fname,
        lname: lname
    };
    console.log(data)
    fetch("/account/update_name", {
        method: "POST",
        headers: {'Content-Type': 'application/json'}, 
        body: JSON.stringify(data)
    }).then(res => {
        console.log("Request complete! response:", res); 
        if(res.status == 201){
            if(fname){
                document.getElementById('first-name-span').innerHTML = fname;
            }
            if(lname){
                document.getElementById('last-name-span').innerHTML = lname;
            }
        }
    });
});

addAccessButton.addEventListener('click', function(){
    console.log("addAccessButton clicked.");
    let grantUserName = document.getElementsByName('add_permission_username')[0].value;
    console.log(grantUserName);
    let data = {
        username: grantUserName
    };
    console.log(data)
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
    console.log(revokeUserName);
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
        console.log(data)
        fetch("/account/update_password", {
            method: "POST",
            headers: {'Content-Type': 'application/json'}, 
            body: JSON.stringify(data)
        }).then(res => {
            console.log("Request complete! response:", res); 
            if(res.status == 201){
                console.log("Password updated successfully.");
                passwordUpdateError.innerHTML = "Password udpated successfully.";
                passwordUpdateError.classList.remove('alert');
                passwordUpdateError.classList.add('success');
                passwordUpdateError.classList.remove('hide');
            }else if(res.status == 401){
                console.log("Password update failed.");
                passwordUpdateError.innerHTML = "Current password incorrect.";
                passwordUpdateError.classList.add('alert');
                passwordUpdateError.classList.remove('hide');
                passwordUpdateError.classList.remove('success');
            }
        });
    }else{
        console.log("New passwords do not match.");
        passwordUpdateError.innerHTML = "Passwords do not match.";
        passwordUpdateError.classList.add('alert');
        passwordUpdateError.classList.remove('success');
        passwordUpdateError.classList.remove('hide');
        console.log(passwordUpdateError);
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
        console.log(data)
        fetch("/account/delete_locations", {
            method: "POST",
            headers: {'Content-Type': 'application/json'}, 
            body: JSON.stringify(data)
        }).then(res => {
            console.log("Request complete! response:", res); 
            if(res.status == 201){
                console.log("Location data deleted.");
                deleteLocationError.innerHTML = "Location data deleted.";
                deleteLocationError.classList.remove('alert');
                deleteLocationError.classList.add('success');
                deleteLocationError.classList.remove('hide');
            }
        });
    }else{
        console.log("Type 'delete' in the text box if you want to remove location data.");
        deleteLocationError.innerHTML = "Type 'delete' in the text box if you want to remove location data.";
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

function loadPlaces() {
    fetch('/api/places')
        .then(function(res) { return res.json(); })
        .then(function(data) {
            var tbody = document.querySelector('#places-table tbody');
            tbody.innerHTML = '';
            data.places.forEach(function(place) {
                var truncWebhook = place.webhook_url.length > 30 ? place.webhook_url.substring(0, 30) + '...' : place.webhook_url;
                var enabledBtnClass = place.enabled ? 'success' : 'secondary';
                var enabledBtnText = place.enabled ? 'On' : 'Off';
                var row = '<tr>' +
                    '<td data-label="Name">' + place.name + '</td>' +
                    '<td data-label="Coordinates" class="show-for-medium">' + place.lat.toFixed(5) + ', ' + place.lon.toFixed(5) + '</td>' +
                    '<td data-label="Radius">' + place.radius + '</td>' +
                    '<td data-label="Webhook" class="show-for-medium" title="' + place.webhook_url + '">' + truncWebhook + '</td>' +
                    '<td data-label="Enabled"><button class="button small ' + enabledBtnClass + '" onclick="togglePlace(' + place.id + ', ' + place.enabled + ')">' + enabledBtnText + '</button></td>' +
                    '<td data-label="Actions">' +
                        '<button class="button small" onclick="viewPlace(' + place.id + ', ' + place.lat + ', ' + place.lon + ', \'' + place.name.replace(/'/g, "\\'") + '\')">View</button> ' +
                        '<button class="button small warning" onclick="editPlace(' + place.id + ', \'' + place.name.replace(/'/g, "\\'") + '\', ' + place.lat + ', ' + place.lon + ', ' + place.radius + ', \'' + place.webhook_url.replace(/'/g, "\\'") + '\', ' + place.enabled + ', ' + (place.include_coords_in_webhook ? 'true' : 'false') + ')">Edit</button> ' +
                        '<button class="button small secondary" onclick="testPlace(' + place.id + ')">Test</button> ' +
                        '<button class="button small alert" onclick="deletePlace(' + place.id + ')">Delete</button>' +
                    '</td>' +
                '</tr>';
                tbody.innerHTML += row;
            });
        });
}

document.addEventListener('DOMContentLoaded', function() {
    loadPlaces();
    document.getElementById('add-place-btn').addEventListener('click', openAddPlaceModal);
});


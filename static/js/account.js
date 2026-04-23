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
function loadPlaces() {
    fetch('/api/places')
        .then(res => res.json())
        .then(data => {
            var tbody = document.querySelector('#places-table tbody');
            tbody.innerHTML = '';
            data.places.forEach(place => {
                var row = `<tr>
                    <td>${place.name}</td>
                    <td>${place.radius}</td>
                    <td>${place.webhook_url}</td>
                    <td>${place.enabled ? 'Yes' : 'No'}</td>
                    <td><button class="button alert" onclick="deletePlace(${place.id})">Delete</button></td>
                </tr>`;
                tbody.innerHTML += row;
            });
        });
}

function deletePlace(id) {
    fetch('/api/places', {
        method: 'DELETE',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: id})
    }).then(() => loadPlaces());
}

document.addEventListener('DOMContentLoaded', loadPlaces);

// Setup Map Picker
// Note: Requires mapboxapi token available in the global JS scope,
// which should be passed from account.html
var placeMap = L.map('place-map').setView([0,0], 2);
L.tileLayer('https://api.mapbox.com/styles/v1/{id}/tiles/{z}/{x}/{y}?access_token=' + mapboxToken, {
    id: 'mapbox/streets-v11',
    accessToken: mapboxToken
}).addTo(placeMap);

var marker;
placeMap.on('click', function(e) {
    if (marker) placeMap.removeLayer(marker);
    marker = L.marker(e.latlng).addTo(placeMap);
});

document.getElementById('save-place-btn').addEventListener('click', function() {
    if (!marker) return;
    fetch('/api/places', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            name: document.getElementById('place-name').value,
            lat: marker.getLatLng().lat,
            lon: marker.getLatLng().lng,
            radius: parseInt(document.getElementById('place-radius').value),
            webhook_url: document.getElementById('place-webhook').value,
            enabled: document.getElementById('place-enabled').checked
        })
    }).then(() => {
        loadPlaces();
        $('#place-modal').foundation('close');
    });
});

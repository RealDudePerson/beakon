
function saveUser() {
    var id = document.getElementById('user-id').value;
    var data = {
        username: document.getElementById('user-username').value,
        password: document.getElementById('user-password').value,
        fname: document.getElementById('user-fname').value,
        lname: document.getElementById('user-lname').value,
        is_admin: document.getElementById('user-is-admin').checked
    };
    
    var url = id ? '/admin/users/' + id + '/update' : '/admin/users/create';
    
    fetch(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
    }).then(res => {
        if(res.ok) {
            location.reload();
        } else {
            alert('Error saving user');
        }
    });
}

function resetToken(id) {
    if(confirm('Are you sure you want to reset this user\'s API token?')) {
        fetch('/admin/users/' + id + '/reset-token', { method: 'POST' })
        .then(res => res.json())
        .then(data => {
            if(data.token) alert('New API token (shown once, store it now): ' + data.token);
            else alert('Error resetting token');
        });
    }
}

function deleteUser(id) {
    if(confirm('Are you sure you want to delete this user and all their data?')) {
        fetch('/admin/users/' + id + '/delete', { method: 'POST' })
        .then(res => {
            if(res.ok) location.reload();
            else alert('Error deleting user');
        });
    }
}

function openEditModal(id) {
    var user = adminUsers.find(function(u) { return u.id === id; });
    if (!user) return;
    document.getElementById('user-modal-title').textContent = 'Edit User';
    document.getElementById('user-id').value = user.id;
    document.getElementById('user-username').value = user.username;
    document.getElementById('user-username').disabled = true;
    document.getElementById('user-password').parentElement.style.display = 'none';
    document.getElementById('user-fname').value = user.fname || '';
    document.getElementById('user-lname').value = user.lname || '';
    document.getElementById('user-is-admin').checked = !!user.is_admin;
    $('#user-modal').foundation('open');
}

function openAddModal() {
    document.getElementById('user-modal-title').textContent = 'Create User';
    document.getElementById('user-id').value = '';
    document.getElementById('user-username').value = '';
    document.getElementById('user-username').disabled = false;
    document.getElementById('user-password').parentElement.style.display = '';
    document.getElementById('user-fname').value = '';
    document.getElementById('user-lname').value = '';
    document.getElementById('user-is-admin').checked = false;
    $('#user-modal').foundation('open');
}

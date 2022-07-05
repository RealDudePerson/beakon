var nameUpdateButton = document.querySelector('#name-update-btn');
var addAccessButton = document.querySelector('#add-access-btn');
var removeAccessButton = document.querySelector('#remove-access-btn');

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

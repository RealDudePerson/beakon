from models import db, UserModel, LocationsModel, SharingPermissionModel, UserDataModel
from datetime import datetime

def seed_test_data():
    # 1. Create Users
    users = ["alice", "bob", "charlie"]
    user_objects = {}
    for username in users:
        user = UserModel(username=username)
        user.set_password("test123")
        db.session.add(user)
        db.session.commit()
        user_objects[username] = user
        
        # Create UserData
        user_data = UserDataModel(id=user.id, fname=username.capitalize(), lname="User")
        db.session.add(user_data)
        
        # 2. Create sample locations (~15 per user)
        for i in range(15):
            loc = LocationsModel()
            loc.set_lat(52.2 + (i * 0.001))
            loc.set_lon(0.1 + (i * 0.001))
            loc.set_acc(10.0)
            loc.set_timestamp(datetime.now())
            loc.set_userid(user.id)
            db.session.add(loc)
    
    # 3. Create sharing permissions
    # alice shares with bob
    perm = SharingPermissionModel()
    perm.set_data_owner_username("alice")
    perm.set_data_owner_id(user_objects["alice"].id)
    perm.set_shared_with_username("bob")
    perm.set_shared_with_id(user_objects["bob"].id)
    db.session.add(perm)
    
    db.session.commit()

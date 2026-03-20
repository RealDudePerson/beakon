from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin, LoginManager

login = LoginManager()
db = SQLAlchemy()

class UserModel(UserMixin, db.Model):
    __tablename__ = 'users'
 
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True)
    password_hash = db.Column(db.String())
    api_token_hash = db.Column(db.String(), unique=True)
 
    def set_password(self,password):
        self.password_hash = generate_password_hash(password)
     
    def check_password(self,password):
        return check_password_hash(self.password_hash,password)

    def set_api_token(self,api_token):
        self.api_token_hash = generate_password_hash(api_token)

    def check_api_token(self,api_token):
        return check_password_hash(self.api_token_hash,api_token)

    def get_username(self):
        return self.username

@login.user_loader
def load_user(id):
    return UserModel.query.get(int(id))

class LocationsModel(db.Model):
    __tablename__ = 'locations_table'

    id = db.Column(db.Integer, primary_key = True)
    lat = db.Column(db.Float())
    lon = db.Column(db.Float())
    acc = db.Column(db.Float())
    timestamp = db.Column(db.DateTime())
    userid = db.Column(db.Integer)
    batt = db.Column(db.Integer)
    ischarging = db.Column(db.Boolean)

    def set_lat(self, lat):
        self.lat = lat
    def get_lat(self):
        return self.lat
    def set_lon(self, lon):
        self.lon = lon
    def get_lon(self):
        return self.lon
    def set_acc(self, acc):
        self.acc = acc
    def get_acc(self):
        return self.acc
    def set_timestamp(self, timestamp):
        self.timestamp = timestamp
    def get_timestamp(self):
        return self.timestamp
    def set_userid(self, userid):
        self.userid = userid
    def get_userid(self):
        return self.userid
    def get_batt(self):
        return self.batt
    def set_batt(self, batt):
        self.batt = batt
    def get_ischarging(self):
        return self.ischarging
    def set_ischarging(self, ischarging):
        self.ischarging = ischarging


class UserDataModel(db.Model):
    __tablename__ = 'user_data'
    id = db.Column(db.Integer, primary_key=True) #this ID is tied to the user ID when setting user data from /updateinfo
    fname = db.Column(db.String(30))
    lname = db.Column(db.String(40))
    access_to = db.Column(db.String(100))
    is_admin = db.Column(db.Boolean())

    def set_fname(self,fname):
        self.fname = fname
    
    def get_fname(self):
        return self.fname

    def set_lname(self, lname):
        self.lname = lname

    def get_lname(self):
        return self.lname
    
    def check_admin(self):
        return self.is_admin

    def check_access(self):
        return self.access_to
    
    def set_admin(self):
        self.is_admin = True

class SharingPermissionModel(db.Model):
    __tablename__ = 'sharing_permission'
    id = db.Column(db.Integer, primary_key=True)
    data_owner_username = db.Column(db.String(100))
    data_owner_id = db.Column(db.Integer)
    shared_with_username = db.Column(db.String(100))
    shared_with_id = db.Column(db.Integer)

    def set_data_owner_username(self,data_owner_username):
        self.data_owner_username = data_owner_username
    
    def get_data_owner_username(self):
        return self.data_owner_username

    def set_data_owner_id(self,data_owner_id):
        self.data_owner_id = data_owner_id
    
    def get_data_owner_id(self):
        return self.data_owner_id

    def set_shared_with_username(self,shared_with_username):
        self.shared_with_username = shared_with_username

    def get_shared_with_username(self):
        return self.shared_with_username
    
    def set_shared_with_id(self,shared_with_id):
        self.shared_with_id = shared_with_id

    def get_shared_with_id(self):
        return self.shared_with_id
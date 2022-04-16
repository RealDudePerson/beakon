import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from flask import Flask, render_template, request, redirect, session
from models import UserModel,db,login,UserDataModel,LocationsModel,SharingPermissionModel
from flask_login import login_required, current_user, login_user, logout_user
from datetime import datetime

app = Flask(__name__)

# Set up logging
app.logger_name = "WEBSRVR"
file_handler = RotatingFileHandler(os.path.join(app.instance_path, 'beakon_webserver.log'), 'a', 1 * 1024 * 1024, 10)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter('%(asctime)s [%(process)-5d:%(thread)#x] %(name)s %(levelname)-5s %(message)s [in %(module)s @ %(pathname)s:%(lineno)d]'))
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.DEBUG)

# Start log
app.logger.info('------------ Starting logs')
app.logger.info('__name__ is \'%s\'' % __name__)

# Load app.config variables from file
resource_path = os.path.dirname(os.path.realpath(os.path.abspath(sys.argv[0]))) + os.sep + 'resource'
app.logger.debug('Looking for custom app config in \'%s\'' % os.path.join(app.instance_path, 'app.cfg'))
app.config.from_pyfile('app.cfg')

# Initiate the database and login
db.init_app(app)
login.init_app(app)
login.login_view = 'login'


# App routes
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect('/dashboard')
    return redirect('/login')

@app.route("/login", methods=['POST','GET'])
def login():
    if current_user.is_authenticated:
        return redirect('/dashboard')
    if request.method == 'POST':
        username = request.form['username']
        user = UserModel.query.filter_by(username = username).first()
        if user is not None and user.check_password(request.form['password']):
            login_user(user)
            return redirect('/dashboard')
    return render_template('login.html')
    

@app.route('/dashboard', methods=['POST','GET'])
@login_required
def dashboard():
    id = current_user.get_id()
    userData = UserDataModel.query.filter_by(id=id).first()
    if userData is not None:
        fname = userData.get_fname()
        lname = userData.get_lname()
        location = LocationsModel.query.filter_by(userid=id).order_by(LocationsModel.id.desc()).first()
        sharing_permission = SharingPermissionModel.query.filter_by(shared_with_id=id).all()
        sharing_permission_count = len(sharing_permission)
        sharing_permission_list = []
        for user in sharing_permission:
            username = user.get_data_owner_username()
            sharing_permission_list.append(username)
        if location is not None:
            lat = location.get_lat()
            lon = location.get_lon()
            timestamp = location.get_timestamp()
            return render_template('dashboard.html',fname=fname,lname=lname,lat=lat,lon=lon,timestamp=timestamp,mapboxapi=app.config['MAPBOX_API_KEY'],sharing_permission_list=sharing_permission_list,sharing_permission_count=sharing_permission_count)
        return render_template('dashboard.html',fname=fname,lname=lname)
    else:
        return render_template('dashboard.html')

@app.route('/checkid')
@login_required
def checkid():
    id = current_user.get_id()
    return(str(id))

@app.route('/logout')
def logout():
    logout_user()
    return redirect('/login')

@app.route('/register', methods=['POST','GET'])
def register():
    if app.config['REGISTRATION_ENABLED']:
        if current_user.is_authenticated:
            return redirect('/dashboard')
        
        if request.method == 'POST':
            username = request.form['username']
            password = request.form['password']

            if UserModel.query.filter_by(username=username).first():
                return ('Username already present')
            
            user = UserModel(username=username)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            return redirect('/login')
        return render_template('register.html')
    else:
        return render_template('registration_closed.html')

@app.route('/recordlocation', methods=['POST','GET'])
@login_required
def record_location():
    if request.method == 'POST':
        request_data = request.get_json()
        lat = request_data['lat']
        lon = request_data['lon']
        acc = request_data['acc']
        location = LocationsModel()
        location.set_lat(lat)
        location.set_lon(lon)
        location.set_acc(acc)
        location.set_timestamp(datetime.now())
        location.set_userid(current_user.get_id())
        db.session.add(location)
        db.session.commit()
        return render_template('recordlocation.html')
    return render_template('recordlocation.html')

#Page used to update personal info and add and remove location sharing permissions
@app.route('/updateinfo', methods=['POST','GET'])
@login_required
def update_info():
    id = current_user.get_id()
    username = UserModel.query.filter_by(id=id).first().get_username()
    if request.method == 'POST':
        fname = request.form['fname']
        lname = request.form['lname']
        userData = UserDataModel.query.filter_by(id=id).first()
        #if user has data, overwrite
        if userData is not None:
            userData.set_fname(fname)
            userData.set_lname(lname)
            db.session.add(userData)
            db.session.commit()
        #if no record exists, create it
        else:
            userData = UserDataModel(id=id)
            userData.set_fname(fname)
            userData.set_lname(lname)
            userData.set_admin() #TODO: Make better way to set admin, likely first user is admin, and only admins can set other admins.
            db.session.add(userData)
            db.session.commit()
    sharing_permission = SharingPermissionModel.query.filter_by(data_owner_id=id).all()
    sharing_permission_count = len(sharing_permission)
    sharing_permission_list = []
    for user in sharing_permission:
        tmp_username = user.get_shared_with_username()
        sharing_permission_list.append(tmp_username)
    fname = UserDataModel.query.filter_by(id=id).first()
    if fname is not None:
        fname = fname.get_fname()
    lname = UserDataModel.query.filter_by(id=id).first()
    if lname is not None:
        lname = lname.get_lname()
    return render_template('update_info.html',username=username,sharing_permission_list=sharing_permission_list,sharing_permission_count=sharing_permission_count,fname=fname,lname=lname,id=id)

@app.route('/updatepermissions/add', methods=['POST'])
@login_required
def add_permissions():
    id = current_user.get_id()
    username = UserModel.query.filter_by(id=id).first().get_username()
    if request.method == 'POST':
        #Verify user exists in the system
        add_permission_username = request.form['add_permission_username']
        shared_with_user = UserModel.query.filter_by(username=add_permission_username).first()
        if shared_with_user is not None:
            #Check if attempting to add permission to self, ignore if so.
            if add_permission_username != username:
                #Only add permission if it does not exist
                permission_record = SharingPermissionModel.query.filter_by(data_owner_id=id,shared_with_username=add_permission_username).first()
                if permission_record is None:
                    add_permission = SharingPermissionModel()
                    add_permission.set_data_owner_username(username)
                    add_permission.set_data_owner_id(id)
                    add_permission.set_shared_with_username(add_permission_username)
                    add_permission.set_shared_with_id(UserModel.query.filter_by(username=add_permission_username).first().get_id())
                    db.session.add(add_permission)
                    db.session.commit()
    return redirect('/updateinfo')

@app.route('/updatepermissions/remove', methods=['POST'])
@login_required
def remove_permissions():
    id = current_user.get_id()
    username = UserModel.query.filter_by(id=id).first().get_username()
    if request.method == 'POST':
        remove_permission_username = request.form['remove_permission_username']
        delete_row = SharingPermissionModel.query.filter_by(data_owner_id=id,shared_with_username=remove_permission_username).delete(synchronize_session=False)
        db.session.commit()
    return redirect('/updateinfo')

@app.route('/map/<map_username>')
@login_required
def map(map_username):
    id = current_user.get_id()
    username = UserModel.query.filter_by(id=id).first().get_username()
    #Verify user record exists otherwise redirect to dashbaord
    map_user = UserModel.query.filter_by(username=map_username).first()
    if map_user is not None:
        map_user_data = UserDataModel.query.filter_by(id=map_user.get_id()).first()
        #Verify that Sharing Permission record exists before showing content, otherwise redirect to dashboard
        has_permission = SharingPermissionModel.query.filter_by(data_owner_id=map_user.get_id(),shared_with_id=id).first()
        if has_permission is not None:
            fname = map_user_data.get_fname()
            lname = map_user_data.get_lname()
            location = LocationsModel.query.filter_by(userid=map_user.get_id()).order_by(LocationsModel.id.desc()).first()
            if location is not None:
                lat = location.get_lat()
                lon = location.get_lon()
                timestamp = location.get_timestamp()
                return render_template('map.html',fname=fname,lname=lname,lat=lat,lon=lon,timestamp=timestamp,mapboxapi=app.config['MAPBOX_API_KEY'])
            return render_template('map.html',fname=fname,lname=lname)
        else:
            return redirect('/dashboard')
    else:
        return redirect('/dashboard')

if __name__ == '__main__':
    @app.before_first_request
    def create_table():
        db.create_all()
    app.run(debug=True,host='0.0.0.0',ssl_context='adhoc')
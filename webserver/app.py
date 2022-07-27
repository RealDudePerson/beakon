import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from flask import Flask, render_template, request, redirect, session, Response
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

# Login page
# TODO: Possibly add some sort of spam prevention
@app.route("/login", methods=['POST','GET'])
def login():
    if current_user.is_authenticated:
        return redirect('/dashboard')
    if request.method == 'POST':
        username = request.form['username'].lower()
        user = UserModel.query.filter_by(username = username).first()
        if user is not None and user.check_password(request.form['password']):
            login_user(user,remember=True)
            app.logger.info('%s logged in successfully', username)
            return redirect('/dashboard')
    return render_template('login.html')
    
# Default page for logged in users
@app.route('/dashboard', methods=['POST','GET'])
@login_required
def dashboard():
    id = current_user.get_id()
    username = current_user.get_username()
    userData = UserDataModel.query.filter_by(id=id).first()
    if userData is not None:
        fname = userData.get_fname()
        lname = userData.get_lname()
    else:
        fname = ""
        lname = ""
    location = LocationsModel.query.filter_by(userid=id).order_by(LocationsModel.id.desc()).first()
    sharing_permission = SharingPermissionModel.query.filter_by(shared_with_id=id).all()
    if sharing_permission:
        sharing_permission_count = len(sharing_permission)
    else:
        sharing_permission_count = 0
    sharing_permission_list = []
    for user in sharing_permission:
        username = user.get_data_owner_username()
        sharing_permission_list.append(username)
    if location is not None:
        lat = location.get_lat()
        lon = location.get_lon()
        batt = location.get_batt()
        ischarging = location.get_ischarging()
        timestamp = location.get_timestamp()
        return render_template('dashboard.html',username=username,fname=fname,lname=lname,lat=lat,lon=lon,timestamp=timestamp,mapboxapi=app.config['MAPBOX_API_KEY'],sharing_permission_list=sharing_permission_list,sharing_permission_count=sharing_permission_count,batt=batt,ischarging=ischarging)
    return render_template('dashboard.html',fname=fname,lname=lname,username=username,sharing_permission_count=sharing_permission_count,sharing_permission_list=sharing_permission_list)

# Used for seeing userid
@app.route('/checkid')
@login_required
def checkid():
    id = current_user.get_id()
    return(str(id))

# Logout page
@app.route('/logout')
def logout():
    logout_user()
    return redirect('/login')

# Registration page
# TODO: set password requirements
# TODO: return error messages in the request when passwords do not match
# TODO: Possibly add some sort of spam prevention
@app.route('/register', methods=['POST','GET'])
def register():
    if app.config['REGISTRATION_ENABLED']:
        if current_user.is_authenticated:
            return redirect('/dashboard')
        
        if request.method == 'POST':
            username = request.form['username'].lower()
            password = request.form['password']

            if UserModel.query.filter_by(username=username).first():
                return ('Username already present')
            
            user = UserModel(username=username)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            app.logger.info('%s registered successfully', username)
            return redirect('/login')
        return render_template('register.html')
    else:
        if 'User-Agent' in request.headers:
            app.logger.info('Registration page accessed: %s', request.headers['User-Agent'])
        else:
            app.logger.info('Registration page accessed by IP without user-agent.')
        return render_template('registration_closed.html')

# Browser based location updater, uses javascript to ping browser for location
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

# Used to set the API token
@app.route('/api/updatetoken', methods=['POST','GET'])
@login_required
def update_token():
    id = current_user.get_id()
    username = UserModel.query.filter_by(id=id).first().get_username()
    if request.method=='POST':
        api_token = request.form['secret']
        if api_token is not None:
            user = UserModel.query.filter_by(id=id).first()
            user.set_api_token(api_token)
            db.session.add(user)
            db.session.commit()
            return redirect('/account')
        else:
            return("Error, api_token incorrect or not found.")
    else:
        return("Error, method is not post")
    
# Api based location updater.
# Use any app to update location with the following headers and data
# headers:
#   username: [username]
#   secret: [API Key created from the 'account/updateinfo' page]
#   Content-Type: application/json
# body:
#   {"lat":"33","lon":"133","acc":"3"}
@app.route('/api/recordlocation', methods=["GET","POST"])
def api_record_location():
    status_code = Response(status=401)
    if request.method == 'POST':
        if request.headers['secret'] and request.headers['username']:
            username = request.headers['username']
            api_token = request.headers['secret']
            user = UserModel.query.filter_by(username=username).first()
            api_token_check = user.check_api_token(api_token)
            if api_token_check == True:
                request_data = request.get_json()
                lat = request_data['lat']
                lon = request_data['lon']
                acc = request_data['acc']
                batt = False
                if 'batt' in request_data:
                    batt = request_data['batt']
                    app.logger.debug('batt is %s', batt)
                ischarging = False
                if 'ischarging' in request_data:
                    ischarging = request_data['ischarging'] in ['true','True']
                    app.logger.debug('ischarging is %s', ischarging)
                location = LocationsModel()
                location.set_lat(lat)
                location.set_lon(lon)
                location.set_acc(acc)
                location.set_timestamp(datetime.now())
                location.set_userid(user.get_id())
                if batt:
                    location.set_batt(batt)
                if ischarging:
                    location.set_ischarging(ischarging)
                db.session.add(location)
                db.session.commit()
                status_code = Response(status=201)
                app.logger.info('%s updated their Location.', username)
                return status_code
    return status_code

# This is where account information can be set and updated
# including adding and removing location permissions
# setting API Token
# and updating display names
@app.route('/account', methods=['GET'])
@login_required
def account():
    id = current_user.get_id()
    username = UserModel.query.filter_by(id=id).first().get_username()
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
    return render_template('account.html',username=username,sharing_permission_list=sharing_permission_list,sharing_permission_count=sharing_permission_count,fname=fname,lname=lname,id=id)

# This is the logic for updating fname, lname
# as well as setting location sharing permissions.
@app.route('/account/<action>', methods=['POST'])
@login_required
def account_action(action):
    id = current_user.get_id()
    username = UserModel.query.filter_by(id=id).first().get_username()
    if action == "add_permission":
        request_data = request.get_json()
        add_permission_username = False
        shared_with_user = None
        if "username" in request_data:
            add_permission_username = request_data['username'].lower()
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
                    app.logger.info('%s allowed %s to view their location.', username, add_permission_username)
                    return Response(status=201)
        else:
            return Response(status=400)
    elif action == "remove_permission":
        request_data = request.get_json()
        remove_permission_username = request_data['username'].lower()
        delete_row = SharingPermissionModel.query.filter_by(data_owner_id=id,shared_with_username=remove_permission_username).delete(synchronize_session=False)
        db.session.commit()
        app.logger.info('%s removed %s from viewing their location.', username, remove_permission_username)
        if(delete_row>0):
            return Response(status=201)
        return Response(status=400)
    elif action == "update_name":
        request_data = request.get_json()
        if 'fname' in request_data:
            fname = request_data['fname']
        if 'lname' in request_data:
            lname = request_data['lname']
        userData = UserDataModel.query.filter_by(id=id).first()
        #If this is the first time the user is setting their information, a userdata db record must be created.
        if userData is None:
            userData = UserDataModel(id=id)
        if fname and lname:
            app.logger.info('%s updated their full name to %s %s', username, fname, lname)
            userData.set_fname(fname)
            userData.set_lname(lname)
        elif lname:
            app.logger.info('%s updated their last name to %s', username, lname)
            userData.set_lname(lname)
        elif fname:
            app.logger.info('%s updated their first name to %s', username, fname)
            userData.set_fname(fname)
        else:
            app.logger.info('%s attempted to set their name, but sent a bad request.', username)
            return Response(status=400)
        db.session.add(userData)
        db.session.commit()
        return Response(status=201)
    elif action == "update_password":
        request_data = request.get_json()
        if 'current_password' in request_data:
            current_password = request_data['current_password']
        if 'new_password' in request_data:
            new_password = request_data['new_password']
        if current_password and new_password:
            if current_user.check_password(current_password):
                current_user.set_password(new_password)
                app.logger.info('%s updated their password', username)
                db.session.add(current_user)
                db.session.commit()
                return Response(status=201)
            else:
                app.logger.info('%s attempted to update their password, but sent a bad current password.', username)
                return Response(status=401)
        else:
            app.logger.info('%s attempted to update their password, but sent a bad request.', username)
            return Response(status=400)
    elif action == "delete_locations":
        LocationsModel.query.filter_by(userid=id).delete(synchronize_session=False)
        db.session.commit()
        app.logger.info('%s deleted their location data.', username)
        return Response(status=201)
    else:
        return Response(status=404)

#Show the location of a single user on a map
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
            try:
                fname = map_user_data.get_fname()
            except:
                fname = None
            try:
                lname = map_user_data.get_lname()
            except:
                lname = None
            location = LocationsModel.query.filter_by(userid=map_user.get_id()).order_by(LocationsModel.id.desc()).first()
            app.logger.debug('%s removed ', location)
            if location is not None:
                lat = location.get_lat()
                lon = location.get_lon()
                timestamp = location.get_timestamp()
                batt = location.get_batt()
                ischarging = location.get_ischarging()
                return render_template('map.html',fname=fname,lname=lname,lat=lat,lon=lon,timestamp=timestamp,mapboxapi=app.config['MAPBOX_API_KEY'],batt=batt,ischarging=ischarging)
            return render_template('map.html',fname=fname,lname=lname) #TODO make template for no location stored yet
        else:
            return redirect('/dashboard')
    else:
        return redirect('/dashboard')

if __name__ == '__main__':
    @app.before_first_request
    def create_table():
        db.create_all()
    app.run(ssl_context="adhoc",host='0.0.0.0')

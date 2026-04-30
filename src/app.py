import logging
import os
import sys
import math
import random
import requests
from logging.handlers import RotatingFileHandler
from flask import Flask, render_template, request, redirect, session, Response, abort
from functools import wraps
from models import UserModel,db,login,UserDataModel,LocationsModel,SharingPermissionModel,KnownPlaceModel
from flask_login import login_required, current_user, login_user, logout_user
from flask_talisman import Talisman
from flask_apscheduler import APScheduler
from datetime import datetime, timedelta
from geofencing import check_geofences, haversine_meters

# Define app
app = Flask(__name__, template_folder='../templates', static_folder='../static', instance_path=os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), 'instance'))

# Ensure instance directory and config exist before loading config
def ensure_paths(app):
    instance_path = app.instance_path
    os.makedirs(instance_path, exist_ok=True)
    logs_path = os.path.join(instance_path, 'logs')
    os.makedirs(logs_path, exist_ok=True)
    project_root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    config_path = os.path.join(project_root, 'config.cfg')
    if not os.path.exists(config_path):
        with open(config_path, 'w') as f:
            f.write("# Auto-generated config\nSECRET_KEY = 'changeme'\nMAPBOX_API_KEY = ''\n")
    return instance_path, logs_path, config_path

ensure_paths(app)

# Set up logging to instance/logs/
app.logger_name = "WEBSRVR"
logs_path = os.path.join(app.instance_path, 'logs')
file_handler = RotatingFileHandler(os.path.join(logs_path, 'beakon.log'), 'a', 1 * 1024 * 1024, 10)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter('%(asctime)s [%(process)-5d:%(thread)#x] %(name)s %(levelname)-5s %(message)s [in %(module)s @ %(pathname)s:%(lineno)d]'))
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.DEBUG)

# Load config
project_root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
config_path = os.path.join(project_root, 'config.cfg')
with open(config_path, 'r') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and value:
                if value.lower() in ('true', '1', 'yes', 'on'):
                    value = True
                elif value.lower() in ('false', '0', 'no', 'off'):
                    value = False
                app.config[key] = value

# Initiate the database and login
db.init_app(app)
login.init_app(app)
login.login_view = 'login'

# Ensure all database tables exist
with app.app_context():
    from models import UserModel, LocationsModel, UserDataModel, SharingPermissionModel, KnownPlaceModel
    db.create_all()

# Initialize Scheduler
scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()
scheduler.add_job(id='geofencing_task', func=check_geofences, args=[app], trigger='interval', minutes=5)

# Cookie security settings
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = False
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['REMEMBER_COOKIE_SECURE'] = True
app.config['REMEMBER_COOKIE_HTTPONLY'] = True
app.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'

# Initialize Talisman with security headers
talisman = Talisman(app,
    content_security_policy={
        'default-src': "'self'",
        'script-src': "'self' 'unsafe-eval' 'unsafe-inline'",
        'style-src': "'self' 'unsafe-inline'",
        'img-src': "'self' data: https://*.tiles.mapbox.com https://api.mapbox.com",
        'connect-src': "'self' https://api.mapbox.com",
    },
    x_xss_protection='1; mode=block'
)

@app.after_request
def add_cross_origin_headers(response):
    response.headers['Cross-Origin-Opener-Policy'] = 'same-origin'
    response.headers['Cross-Origin-Resource-Policy'] = 'same-origin'
    response.headers['Cross-Origin-Embedder-Policy'] = 'credentialless'
    return response

def format_timestamp(ts, time_only=False):
    if time_only:
        return ts.strftime('%H:%M:%S')
    diff = datetime.now() - ts
    if diff.days < 1:
        hours_ago = diff.seconds // 3600
        if hours_ago >= 1:
            return f"{hours_ago} hour{'s' if hours_ago != 1 else ''} ago"
        minutes_ago = diff.seconds // 60
        if minutes_ago >= 1:
            return f"{minutes_ago} minute{'s' if minutes_ago != 1 else ''} ago"
        return "just now"
    return str(ts)

def get_filtered_locations(userid, max_locations=10, min_distance_meters=91):
    all_locations = LocationsModel.query.filter_by(userid=userid).order_by(LocationsModel.timestamp.desc()).yield_per(100)
    filtered = []
    last_loc = None
    for loc in all_locations:
        if last_loc:
            dist = haversine_meters(last_loc.get_lat(), last_loc.get_lon(), loc.get_lat(), loc.get_lon())
            if dist < min_distance_meters:
                continue
        filtered.append(loc)
        last_loc = loc
        if len(filtered) >= max_locations:
            break
    return filtered

def get_locations_for_date(userid, date_str):
    date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
    start_of_day = datetime.combine(date_obj, datetime.min.time())
    end_of_day = datetime.combine(date_obj, datetime.max.time())
    return LocationsModel.query.filter(
        LocationsModel.userid == userid,
        LocationsModel.timestamp >= start_of_day,
        LocationsModel.timestamp <= end_of_day
    ).order_by(LocationsModel.timestamp.asc()).all()

# Context processor to make user admin status available globally to all templates
@app.context_processor
def inject_admin_status():
    is_admin = False
    if current_user.is_authenticated:
        user_data = UserDataModel.query.filter_by(id=current_user.id).first()
        if user_data and user_data.is_admin:
            is_admin = True
    return dict(is_admin=is_admin)

# Admin required decorator
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect('/login')
        user_data = UserDataModel.query.filter_by(id=current_user.id).first()
        if not user_data or not user_data.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

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
    
    locations = get_filtered_locations(id)
    sharing_permission = SharingPermissionModel.query.filter_by(shared_with_id=id).all()
    if sharing_permission:
        sharing_permission_count = len(sharing_permission)
    else:
        sharing_permission_count = 0
    sharing_permission_list = []
    for user in sharing_permission:
        username = user.get_data_owner_username()
        sharing_permission_list.append(username)
    
    if locations:
        location = locations[0]
        lat = location.get_lat()
        lon = location.get_lon()
        batt = location.get_batt()
        ischarging = location.get_ischarging()
        timestamp = format_timestamp(location.get_timestamp())
        
        locations_data = []
        for i, loc in enumerate(locations):
            locations_data.append({
                'lat': loc.get_lat(),
                'lon': loc.get_lon(),
                'timestamp': format_timestamp(loc.get_timestamp()),
                'batt': loc.get_batt(),
                'ischarging': loc.get_ischarging(),
                'index': i
            })
        
        return render_template('dashboard.html',
            username=username, fname=fname, lname=lname,
            lat=lat, lon=lon, timestamp=timestamp,
            mapboxapi=app.config['MAPBOX_API_KEY'],
            sharing_permission_list=sharing_permission_list,
            sharing_permission_count=sharing_permission_count,
            batt=batt, ischarging=ischarging,
            locations=locations_data)
    
    return render_template('dashboard.html',
        fname=fname, lname=lname, username=username,
        sharing_permission_count=sharing_permission_count,
        sharing_permission_list=sharing_permission_list)

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
            
            # Check if this is the first user
            is_first_user = UserModel.query.count() == 0
            
            user = UserModel(username=username)
            user.set_password(password)
            db.session.add(user)
            db.session.commit() # Commit to get the user.id
            
            # Create UserDataModel with admin status
            user_data = UserDataModel(id=user.id, is_admin=is_first_user)
            db.session.add(user_data)
            db.session.commit()
            
            app.logger.info('%s registered successfully (admin: %s)', username, is_first_user)
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
        
        batt = request_data.get('batt')
        if batt is not None:
            location.set_batt(batt)
        
        ischarging = request_data.get('ischarging', False)
        location.set_ischarging(ischarging)
        
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

@app.route('/api/locations', methods=['GET'])
@login_required
def api_get_locations():
    id = current_user.get_id()
    username = UserModel.query.filter_by(id=id).first().get_username()
    app.logger.debug('%s fetched their own locations via API', username)
    
    date_str = request.args.get('date')
    if date_str:
        locations = get_locations_for_date(id, date_str)
        time_only = True
    else:
        locations = get_filtered_locations(id)
        time_only = False
    
    if locations:
        latest = locations[-1] if date_str else locations[0]
        return {
            'locations': [{
                'lat': loc.get_lat(),
                'lon': loc.get_lon(),
                'timestamp': format_timestamp(loc.get_timestamp(), time_only),
                'batt': loc.get_batt(),
                'ischarging': loc.get_ischarging()
            } for loc in locations],
            'latest': {
                'lat': latest.get_lat(),
                'lon': latest.get_lon(),
                'timestamp': format_timestamp(latest.get_timestamp(), time_only),
                'batt': latest.get_batt(),
                'ischarging': latest.get_ischarging()
            }
        }
    return {'locations': [], 'latest': None}

@app.route('/api/locations/<map_username>', methods=['GET'])
@login_required
def api_get_user_locations(map_username):
    id = current_user.get_id()
    username = UserModel.query.filter_by(id=id).first().get_username()
    map_user = UserModel.query.filter_by(username=map_username).first()
    if map_user is None:
        return {'error': 'User not found'}, 404
    has_permission = SharingPermissionModel.query.filter_by(
        data_owner_id=map_user.get_id(), shared_with_id=id
    ).first()
    if has_permission is None:
        return {'error': 'Permission denied'}, 403
    app.logger.info("%s viewed %s's location via API", username, map_username)
    
    date_str = request.args.get('date')
    if date_str:
        locations = get_locations_for_date(map_user.get_id(), date_str)
        time_only = True
    else:
        locations = get_filtered_locations(map_user.get_id())
        time_only = False
    
    if locations:
        latest = locations[-1] if date_str else locations[0]
        return {
            'locations': [{
                'lat': loc.get_lat(),
                'lon': loc.get_lon(),
                'timestamp': format_timestamp(loc.get_timestamp(), time_only),
                'batt': loc.get_batt(),
                'ischarging': loc.get_ischarging()
            } for loc in locations],
            'latest': {
                'lat': latest.get_lat(),
                'lon': latest.get_lon(),
                'timestamp': format_timestamp(latest.get_timestamp(), time_only),
                'batt': latest.get_batt(),
                'ischarging': latest.get_ischarging()
            }
        }
    return {'locations': [], 'latest': None}

# Known Places API
@app.route('/api/places', methods=['GET', 'POST', 'PUT', 'DELETE'])
@login_required
def api_places():
    id = current_user.get_id()
    if request.method == 'GET':
        places = KnownPlaceModel.query.filter_by(userid=id).all()
        return {'places': [{'id': p.id, 'name': p.name, 'lat': p.lat, 'lon': p.lon, 'radius': p.radius, 'webhook_url': p.webhook_url, 'enabled': p.enabled} for p in places]}
    elif request.method == 'POST':
        data = request.get_json()
        place = KnownPlaceModel(userid=id, name=data['name'], lat=data['lat'], lon=data['lon'], radius=data.get('radius', 100), webhook_url=data['webhook_url'], enabled=data.get('enabled', True))
        db.session.add(place)
        db.session.commit()
        return Response(status=201)
    elif request.method == 'PUT':
        data = request.get_json()
        place = KnownPlaceModel.query.filter_by(id=data['id'], userid=id).first()
        if not place:
            return Response(status=404)
        if 'name' in data: place.name = data['name']
        if 'lat' in data: place.lat = data['lat']
        if 'lon' in data: place.lon = data['lon']
        if 'radius' in data: place.radius = data['radius']
        if 'webhook_url' in data: place.webhook_url = data['webhook_url']
        if 'enabled' in data: place.enabled = data['enabled']
        db.session.commit()
        return Response(status=200)
    elif request.method == 'DELETE':
        data = request.get_json()
        KnownPlaceModel.query.filter_by(id=data['id'], userid=id).delete()
        db.session.commit()
        return Response(status=200)
    return Response(status=405)

@app.route('/api/places/<int:place_id>/test', methods=['POST'])
@login_required
def api_test_place(place_id):
    id = current_user.get_id()
    place = KnownPlaceModel.query.filter_by(id=place_id, userid=id).first()
    if not place:
        return Response(status=404)
    
    payload = {
        "event": "test",
        "place": place.name,
        "username": current_user.get_username(),
        "fname": "Test",
        "lname": "User"
    }
    try:
        response = requests.post(place.webhook_url, json=payload, timeout=5)
        return {'status': response.status_code}, 200
    except Exception as e:
        app.logger.error(f"Test webhook failed for place {place.name}: {e}")
        return {'error': str(e)}, 500

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
    return render_template('account.html',username=username,sharing_permission_list=sharing_permission_list,sharing_permission_count=sharing_permission_count,fname=fname,lname=lname,id=id, mapboxapi=app.config['MAPBOX_API_KEY'])

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
    map_user = UserModel.query.filter_by(username=map_username).first()
    if map_user is not None:
        map_user_data = UserDataModel.query.filter_by(id=map_user.get_id()).first()
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
            
            locations = get_filtered_locations(map_user.get_id())
            app.logger.info("%s viewed %s's location", username, map_username)
            
            if locations:
                location = locations[0]
                lat = location.get_lat()
                lon = location.get_lon()
                timestamp = format_timestamp(location.get_timestamp())
                batt = location.get_batt()
                ischarging = location.get_ischarging()
                
                locations_data = []
                for i, loc in enumerate(locations):
                    locations_data.append({
                        'lat': loc.get_lat(),
                        'lon': loc.get_lon(),
                        'timestamp': format_timestamp(loc.get_timestamp()),
                        'batt': loc.get_batt(),
                        'ischarging': loc.get_ischarging(),
                        'index': i
                    })
                
                return render_template('map.html',
                    fname=fname, lname=lname, lat=lat, lon=lon,
                    timestamp=timestamp, mapboxapi=app.config['MAPBOX_API_KEY'],
                    batt=batt, ischarging=ischarging, locations=locations_data,
                    map_username=map_username)
            
            return render_template('map.html', fname=fname, lname=lname)
        else:
            return redirect('/dashboard')
    else:
        return redirect('/dashboard')

@app.route('/speed')
def speed():
    return render_template('speed.html')

# Admin Dashboard
@app.route('/admin', methods=['GET'])
@login_required
@admin_required
def admin_dashboard():
    users = UserModel.query.all()
    user_list = []
    for u in users:
        u_data = UserDataModel.query.filter_by(id=u.id).first()
        is_admin = u_data.is_admin if u_data else False
        fname = u_data.fname if u_data else ''
        lname = u_data.lname if u_data else ''
        user_list.append({
            'id': u.id,
            'username': u.username,
            'fname': fname,
            'lname': lname,
            'is_admin': is_admin
        })
    return render_template('admin.html', users=user_list)


@app.route('/admin/users/create', methods=['POST'])
@login_required
@admin_required
def admin_create_user():
    data = request.get_json()
    username = data['username'].lower()
    if UserModel.query.filter_by(username=username).first():
        return {'error': 'Username exists'}, 400
    
    user = UserModel(username=username)
    user.set_password(data['password'])
    db.session.add(user)
    db.session.commit()
    
    user_data = UserDataModel(id=user.id, fname=data.get('fname'), lname=data.get('lname'), is_admin=data.get('is_admin', False))
    db.session.add(user_data)
    db.session.commit()
    return Response(status=201)

@app.route('/admin/users/<int:user_id>/update', methods=['POST'])
@login_required
@admin_required
def admin_update_user(user_id):
    data = request.get_json()
    user_data = UserDataModel.query.filter_by(id=user_id).first()
    if not user_data:
        return {'error': 'User data not found'}, 404
        
    user_data.fname = data.get('fname', user_data.fname)
    user_data.lname = data.get('lname', user_data.lname)
    user_data.is_admin = data.get('is_admin', False)
    db.session.commit()
    return Response(status=200)

@app.route('/admin/users/<int:user_id>/reset-token', methods=['POST'])
@login_required
@admin_required
def admin_reset_token(user_id):
    user = UserModel.query.get(user_id)
    if not user:
        return {'error': 'User not found'}, 404
    import secrets
    user.set_api_token(secrets.token_hex(16))
    db.session.commit()
    return Response(status=200)

@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_user(user_id):
    user = UserModel.query.get(user_id)
    if not user:
        return {'error': 'User not found'}, 404
    db.session.delete(user)
    db.session.commit()
    return Response(status=200)

if __name__ == '__main__':
    app.run(ssl_context="adhoc",host='0.0.0.0')

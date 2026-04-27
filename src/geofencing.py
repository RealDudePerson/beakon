import requests
import math
from models import db, LocationsModel, KnownPlaceModel, UserModel

def haversine_meters(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def check_geofences(app):
    ctx = app.app_context()
    ctx.push()
    try:
        places = KnownPlaceModel.query.filter_by(enabled=True).all()
        for place in places:
            user = UserModel.query.get(place.userid)
            if not user:
                continue
            
            latest_loc = LocationsModel.query.filter_by(userid=place.userid).order_by(LocationsModel.timestamp.desc()).first()
            if not latest_loc:
                continue
                
            dist = haversine_meters(latest_loc.lat, latest_loc.lon, place.lat, place.lon)
            is_inside = dist < place.radius
            
            if is_inside != place.is_inside:
                place.is_inside = is_inside
                db.session.commit()
                
                # Fire webhook
                event = "entry" if is_inside else "exit"
                payload = {
                    "event": event,
                    "place": place.name,
                    "username": user.username
                }
                
                # Get user info if available
                from models import UserDataModel
                user_data = UserDataModel.query.filter_by(id=user.id).first()
                if user_data:
                    payload["fname"] = user_data.fname
                    payload["lname"] = user_data.lname
                    
                try:
                    requests.post(place.webhook_url, json=payload, timeout=5)
                except Exception as e:
                    import logging
                    logging.error(f"Webhook failed for place {place.name}: {e}")
    finally:
        ctx.pop()

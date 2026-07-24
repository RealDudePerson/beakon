import logging
import math
from datetime import datetime

import requests
from sqlalchemy import func

from models import db, KnownPlaceModel, LocationsModel, UserDataModel, UserModel

def haversine_meters(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def _batch_fetch_users_and_locations(places):
    userids = list({p.userid for p in places})
    if not userids:
        return {}, {}, {}

    users = UserModel.query.filter(UserModel.id.in_(userids)).all()
    user_map = {u.id: u for u in users}

    subq = (
        db.session.query(
            LocationsModel.userid,
            func.max(LocationsModel.id).label("max_id"),
        )
        .filter(LocationsModel.userid.in_(userids))
        .group_by(LocationsModel.userid)
        .subquery()
    )

    latest_locs = (
        db.session.query(LocationsModel)
        .join(subq, LocationsModel.id == subq.c.max_id)
        .all()
    )
    loc_map = {loc.userid: loc for loc in latest_locs}

    user_data_list = UserDataModel.query.filter(UserDataModel.id.in_(userids)).all()
    user_data_map = {ud.id: ud for ud in user_data_list}

    return user_map, loc_map, user_data_map

def check_geofences(app):
    ctx = app.app_context()
    ctx.push()
    try:
        places = KnownPlaceModel.query.filter_by(enabled=True).all()
        user_map, loc_map, user_data_map = _batch_fetch_users_and_locations(places)

        for place in places:
            user = user_map.get(place.userid)
            if not user:
                continue

            latest_loc = loc_map.get(place.userid)
            if not latest_loc or latest_loc.lat is None or latest_loc.lon is None:
                continue

            dist = haversine_meters(latest_loc.lat, latest_loc.lon, place.lat, place.lon)
            is_inside = dist < place.radius

            if is_inside == place.is_inside:
                continue

            place.is_inside = is_inside

            event = "arrived" if is_inside else "left"
            payload = {
                "event": event,
                "place": place.name,
                "username": user.username,
            }
            if place.include_coords_in_webhook:
                payload["lat"] = latest_loc.lat
                payload["lon"] = latest_loc.lon

            user_data = user_data_map.get(user.id)
            if user_data:
                payload["fname"] = user_data.fname
                payload["lname"] = user_data.lname

            now = datetime.now()
            try:
                response = requests.post(place.webhook_url, json=payload, timeout=5)
                place.last_webhook_time = now
                place.last_webhook_status = "success" if response.status_code == 200 else f"error:{response.status_code}"
            except Exception as e:
                logging.error("Webhook failed for place %s: %s", place.name, e)
                place.last_webhook_time = now
                place.last_webhook_status = "failed"

            db.session.commit()
    finally:
        ctx.pop()

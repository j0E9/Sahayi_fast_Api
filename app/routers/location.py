# app/routers/location.py
from __future__ import annotations

from datetime import datetime
from typing import Optional, List

import requests
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, confloat
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, SavedLocation
from app.settings import settings

router = APIRouter(prefix="", tags=["location"])  # no prefix to match your old paths

# ------------- Dependencies ------------- #
def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """
    Trust API-style session auth:
    We store user_id in request.session["user_id"] when logging in.
    """
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = db.query(User).get(int(uid))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user

# ------------- Schemas ------------- #
class GeoPoint(BaseModel):
    latitude: confloat(ge=-90, le=90)   # type: ignore
    longitude: confloat(ge=-180, le=180)  # type: ignore

class LocationIn(GeoPoint):
    state: Optional[str] = None
    zipcode: Optional[str] = None
    name: Optional[str] = None

class LocationOut(BaseModel):
    id: int
    name: str
    state: Optional[str] = None
    zipcode: Optional[str] = None
    latitude: float
    longitude: float

class GetLocDetailsIn(GeoPoint):
    pass

# --- replace your GetLocDetailsOut with this ---
# --- in app/routers/location.py ---

class GetLocDetailsOut(BaseModel):
    state: str = Field(default="Unknown")
    zipcode: str = Field(default="Unknown")
    city: Optional[str] = None
    label: Optional[str] = None

def _extract_mapbox(fields: dict) -> GetLocDetailsOut:
    features = fields.get("features") or []
    if not features:
        return GetLocDetailsOut()

    f0 = features[0]
    context = f0.get("context", []) or []
    all_feats = [f0] + context

    state = None
    zipcode = None
    city = None

    # Scan both top feature and context
    for item in all_feats:
        fid = item.get("id", "")
        txt = item.get("text")
        if not fid or not txt:
            continue

        if fid.startswith("region."):
            state = txt
        elif fid.startswith("postcode."):
            zipcode = txt
        elif fid.startswith("place."):
            city = txt
        elif fid.startswith("district.") and not city:
            # district can be useful in India when 'place' is absent
            city = txt

    label = f0.get("place_name")

    return GetLocDetailsOut(
        state=state or "Unknown",
        zipcode=zipcode or "Unknown",
        city=city,
        label=label
    )

def _mapbox_reverse_geocode(lat: float, lon: float) -> GetLocDetailsOut:
    token = settings.MAPBOX_TOKEN or ""
    if not token:
        # dev token; replace with your own
        token = "pk.eyJ1IjoiajBpMyIsImEiOiJjbWc5MHducmMwYWp5MmpzNmdvcTkxbHJlIn0.NHz6uC-dHVysD-SvDLmAgw"

    url = (
        f"https://api.mapbox.com/geocoding/v5/mapbox.places/"
        f"{lon},{lat}.json?access_token={token}&language=en&limit=1"
    )

    try:
        r = requests.get(url, timeout=6)
        r.raise_for_status()
        data = r.json()
        out = _extract_mapbox(data)
        print("DEBUG Mapbox ->", out.dict())
        return out
    except Exception as e:
        print("Mapbox error:", repr(e))
        return GetLocDetailsOut()

def _nominatim_reverse_geocode(lat: float, lon: float) -> GetLocDetailsOut:
    """
    OpenStreetMap Nominatim fallback. Be nice: set a UA, small timeouts.
    """
    url = "https://nominatim.openstreetmap.org/reverse"
    params = {
        "format": "jsonv2",
        "lat": str(lat),
        "lon": str(lon),
        "addressdetails": "1"
    }
    headers = {
        "User-Agent": "SJF-Tech-JobConnect/1.0 (contact: admin@example.com)"
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=6)
        r.raise_for_status()
        j = r.json()
        addr = j.get("address", {}) or {}

        state = addr.get("state") or "Unknown"
        zipcode = addr.get("postcode") or "Unknown"
        # prefer city/town/village/suburb
        city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("suburb")
        label = j.get("display_name")

        out = GetLocDetailsOut(state=state, zipcode=zipcode, city=city, label=label)
        print("DEBUG OSM ->", out.dict())
        return out
    except Exception as e:
        print("OSM error:", repr(e))
        return GetLocDetailsOut()

def reverse_geocode(lat: float, lon: float) -> GetLocDetailsOut:
    """
    Try Mapbox first; if it yields Unknowns, fall back to OSM.
    """
    m = _mapbox_reverse_geocode(lat, lon)
    if (m.state and m.state != "Unknown") or (m.zipcode and m.zipcode != "Unknown"):
        return m

    # Fallback to OSM if Mapbox failed or yielded only Unknowns
    o = _nominatim_reverse_geocode(lat, lon)
    return o

@router.post("/get_location_details", response_model=GetLocDetailsOut)
def get_location_details(data: GetLocDetailsIn):
    try:
        return reverse_geocode(data.latitude, data.longitude)
    except Exception as e:
        print("reverse_geocode fatal:", repr(e))
        return GetLocDetailsOut(state="Unknown", zipcode="Unknown")

# --- add these routes back ---

@router.post("/update_location")
def update_location(
    data: LocationIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user.latitude = float(data.latitude)
    current_user.longitude = float(data.longitude)
    current_user.state = data.state
    current_user.zipcode = data.zipcode
    db.add(current_user)
    db.commit()
    return {"status": "success"}


@router.post("/save_location")
def save_location(
    data: LocationIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if data.latitude is None or data.longitude is None:
        raise HTTPException(status_code=400, detail="Missing coordinates")

    zipcode = (data.zipcode or "Unknown").strip()
    name = (data.name or "").strip() or f"Place ({zipcode})"

    loc = SavedLocation(
        user_id=current_user.id,
        name=name,
        latitude=float(data.latitude),
        longitude=float(data.longitude),
        state=data.state,
        zipcode=zipcode,
    )
    db.add(loc)
    db.commit()
    db.refresh(loc)
    return {"success": True, "message": f"Location '{name}' saved!"}


@router.get("/get_saved_locations", response_model=List[LocationOut])
def get_saved_locations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(SavedLocation)
        .filter(SavedLocation.user_id == current_user.id)
        .all()
    )
    if not rows:
        return []
    return [
        LocationOut(
            id=r.id,
            name=r.name,
            state=r.state,
            zipcode=r.zipcode,
            latitude=float(r.latitude),
            longitude=float(r.longitude),
        )
        for r in rows
    ]


@router.post("/set_current_location")
def set_current_location(
    data: LocationIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if data.latitude is None or data.longitude is None:
        raise HTTPException(status_code=400, detail="Missing coordinates")

    current_user.latitude = float(data.latitude)
    current_user.longitude = float(data.longitude)
    current_user.state = data.state
    current_user.zipcode = data.zipcode
    db.add(current_user)
    db.commit()
    return {"success": True, "message": "Current location updated!"}

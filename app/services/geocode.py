# app/services/geocode.py
import requests

def reverse_geocode(lat: float, lon: float) -> str:
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}"
        headers = {"User-Agent": "Sahayi/1.0 (contact: support@example.com)"}
        r = requests.get(url, headers=headers, timeout=8)
        r.raise_for_status()
        data = r.json()
        addr = data.get("address", {}) or {}
        return addr.get("city") or addr.get("town") or addr.get("village") or "Unknown"
    except Exception:
        return "Unknown"

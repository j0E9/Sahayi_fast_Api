from datetime import datetime
import pytz

def ist_now():
    return datetime.now(pytz.timezone("Asia/Kolkata")).replace(tzinfo=None)

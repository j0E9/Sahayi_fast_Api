# app/redis_client.py
import os
import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

# decode_responses=True returns strings (not bytes)
r = redis.from_url(REDIS_URL, decode_responses=True)

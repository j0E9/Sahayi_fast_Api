import requests
import json

url = 'http://127.0.0.1:8000/admin/kyc/update_status'
payload = {'user_id': 1, 'status': 'approved'}
try:
    r = requests.post(url, json=payload, timeout=5)
    print('STATUS', r.status_code)
    print(r.text)
except Exception as e:
    print('ERROR', e)

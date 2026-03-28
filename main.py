from flask import Flask, jsonify, request
import requests
from bs4 import BeautifulSoup
import re
import sqlite3
import os
import threading
import hashlib
import time
from datetime import datetime, timedelta

app = Flask(__name__)

# --- Configuration ---
BOT_NAME = "OTP Bot API"
USERNAME = "faysal91"
PASSWORD = "faysal91"
DB_FILE = "sms_database_np.db"

# --- API Endpoints ---
BASE_URL = "http://91.232.105.47/ints"
DOMAIN_URL = "http://217.182.195.194"
LOGIN_PAGE_URL = f"{BASE_URL}/"
SMS_HTML_PAGE_URL = f"{BASE_URL}/agent/SMSCDRReports"

POTENTIAL_API_URLS = [
    f"{BASE_URL}/agent/res/data_smscdr.php",
    f"{DOMAIN_URL}/res/data_smscdr.php",
    f"{BASE_URL}/res/data_smscdr.php"
]

# --- Global variables ---
db_connection = None
session = None
working_api_url = None
latest_sms_data = []
lock = threading.Lock()

# --- Database Functions ---
def setup_database():
    global db_connection
    try:
        db_connection = sqlite3.connect(DB_FILE, check_same_thread=False)
        cursor = db_connection.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS sms_cache (hash TEXT PRIMARY KEY, data TEXT, timestamp TEXT)')
        db_connection.commit()
        print(f"[*] Database connected.")
        return True
    except sqlite3.Error as e:
        print(f"[!!!] DATABASE ERROR: {e}")
        return False

def solve_math_captcha(captcha_text):
    match = re.search(r'(\d+)\s*([+*])\s*(\d+)', captcha_text)
    if not match:
        return None
    n1, op, n2 = int(match.group(1)), match.group(2), int(match.group(3))
    result = n1 + n2 if op == '+' else n1 * n2
    print(f"[*] Solved Captcha: {n1} {op} {n2} = {result}")
    return result

def login_to_panel():
    """Login to the SMS panel and return session"""
    global session
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'
    })
    
    try:
        print("[*] Logging in...")
        r = session.get(LOGIN_PAGE_URL, timeout=20)
        soup = BeautifulSoup(r.text, 'html.parser')
        form = soup.find('form')
        
        if not form:
            raise Exception("Could not find <form> tag.")
        
        post_url = form.get('action')
        if not post_url.startswith('http'):
            post_url = f"{BASE_URL}/{post_url.lstrip('/')}"
        
        payload = {}
        for tag in form.find_all('input'):
            n, v, p = tag.get('name'), tag.get('value', ''), tag.get('placeholder', '').lower()
            if not n:
                continue
            if 'user' in p:
                payload[n] = USERNAME
            elif 'pass' in p:
                payload[n] = PASSWORD
            elif 'ans' in p:
                el = soup.find(string=re.compile(r'What is \d+ \s*[+*]\s* \d+'))
                if not el:
                    raise Exception("Could not find captcha text.")
                payload[n] = solve_math_captcha(el)
            else:
                payload[n] = v
        
        r = session.post(post_url, data=payload, headers={'Referer': LOGIN_PAGE_URL})
        
        if "dashboard" in r.url.lower() or "Logout" in r.text:
            print("[SUCCESS] Authentication complete!")
            return True
        else:
            print("[!!!] AUTHENTICATION FAILED.")
            return False
    except Exception as e:
        print(f"[!!!] Login error: {e}")
        return False

def find_working_api():
    """Find the working API URL"""
    global working_api_url
    if working_api_url:
        return working_api_url
    
    for url_to_test in POTENTIAL_API_URLS:
        try:
            test_response = session.get(url_to_test, timeout=20, params={'sEcho': '1'})
            if test_response.status_code != 404:
                print(f"[SUCCESS] Found working API URL: {url_to_test}")
                working_api_url = url_to_test
                return working_api_url
        except requests.exceptions.RequestException:
            pass
    return None

def fetch_sms_data():
    """Fetch SMS data and convert to requested format"""
    global latest_sms_data, working_api_url
    
    try:
        if not working_api_url:
            working_api_url = find_working_api()
            if not working_api_url:
                print("[!!!] No working API URL found")
                return []
        
        date_to = datetime.now()
        date_from = datetime.now() - timedelta(days=1)
        
        params = {
            'fdate1': date_from.strftime('%Y-%m-%d %H:%M:%S'),
            'fdate2': date_to.strftime('%Y-%m-%d %H:%M:%S')
        }
        
        api_headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": SMS_HTML_PAGE_URL
        }
        
        response = session.get(working_api_url, params=params, headers=api_headers, timeout=30)
        response.raise_for_status()
        json_data = response.json()
        
        formatted_data = []
        
        if 'aaData' in json_data and isinstance(json_data['aaData'], list):
            sms_list = json_data['aaData']
            
            for sms_data in sms_list:
                if len(sms_data) > 5:
                    dt = str(sms_data[0])      # Date/Time
                    rc = str(sms_data[2])      # Recipient Number
                    sn = str(sms_data[3])      # Sender Name
                    msg = str(sms_data[5])     # Message Content
                    
                    # Skip invalid data
                    if not msg or not rc or rc.strip() == '0' or len(rc.strip()) < 6:
                        continue
                    
                    # Format: [Sender, Phone, Message, DateTime]
                    formatted_entry = [
                        sn,      # Sender (e.g., "WhatsApp")
                        rc,      # Phone Number (e.g., "263781697758")
                        msg,     # Message (e.g., "Your WhatsApp code 302-568...")
                        dt       # DateTime (e.g., "2026-03-28 14:01:52")
                    ]
                    formatted_data.append(formatted_entry)
        
        # Update latest data with thread safety
        with lock:
            latest_sms_data = formatted_data
        
        print(f"[*] Fetched {len(formatted_data)} SMS entries")
        return formatted_data
        
    except Exception as e:
        print(f"[!] Error fetching SMS: {e}")
        return []

def background_worker():
    """Background thread to periodically fetch SMS data"""
    while True:
        try:
            fetch_sms_data()
            time.sleep(5)  # Fetch every 5 seconds
        except Exception as e:
            print(f"[!] Background worker error: {e}")
            time.sleep(10)

# --- Flask Routes ---

@app.route('/')
def home():
    """Root endpoint - shows API status"""
    return jsonify({
        "status": "running",
        "bot_name": BOT_NAME,
        "total_sms": len(latest_sms_data),
        "endpoints": {
            "/": "API Status",
            "/sms": "Get all SMS data",
            "/latest": "Get latest SMS only"
        }
    })

@app.route('/sms')
def get_all_sms():
    """Return all SMS data in requested format"""
    with lock:
        return jsonify(latest_sms_data)

@app.route('/latest')
def get_latest_sms():
    """Return only the latest SMS"""
    with lock:
        if latest_sms_data:
            return jsonify([latest_sms_data[-1]])  # Return as array with single item
        return jsonify([])

@app.route('/refresh')
def manual_refresh():
    """Manually trigger a refresh"""
    data = fetch_sms_data()
    return jsonify({
        "status": "refreshed",
        "count": len(data),
        "data": data
    })

# --- Main ---
if __name__ == '__main__':
    print("="*60)
    print("--- OTP Bot API Server (Render Compatible) ---")
    print("="*60)
    
    # Setup database
    setup_database()
    
    # Login to panel
    if login_to_panel():
        # Start background worker thread
        worker_thread = threading.Thread(target=background_worker, daemon=True)
        worker_thread.start()
        print("[*] Background worker started")
        
        # Initial fetch
        fetch_sms_data()
    else:
        print("[!!!] Failed to login, starting anyway...")
    
    # Get port from environment (Render sets this)
    port = int(os.environ.get('PORT', 5000))
    
    # Run Flask app
    app.run(host='0.0.0.0', port=port, debug=False)
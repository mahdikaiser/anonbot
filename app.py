# app.py
import os
import json
import hmac
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify
import requests

# ---------- تنظیمات محیط ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_CHAT_ID = os.environ.get("OWNER_CHAT_ID", "").strip()
BASE_URL = os.environ.get("BASE_URL", "").strip()
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
ANON_SALT = os.environ.get("ANON_SALT", "")
JSON_PATH = Path("messages.json")

if not BOT_TOKEN or not OWNER_CHAT_ID or not BASE_URL:
    raise RuntimeError("❌ لطفاً BOT_TOKEN, OWNER_CHAT_ID, BASE_URL را در env تنظیم کنید.")

# ---------- تنظیمات عمومی ----------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("anonbot")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
WEBHOOK_URL = f"{BASE_URL}/webhook/{WEBHOOK_SECRET}"

app = Flask(__name__)

# ---------- توابع کمکی ----------
def ensure_json():
    if not JSON_PATH.exists():
        JSON_PATH.write_text(json.dumps({"messages": []}, ensure_ascii=False, indent=2), encoding="utf-8")

def load_data():
    ensure_json()
    return json.loads(JSON_PATH.read_text(encoding="utf-8"))

def save_data(data):
    JSON_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def utc_now_iso():
    return datetime.utcnow().isoformat() + "Z"

def make_anon_id(user_id: int) -> str:
    raw = hmac.new(ANON_SALT.encode(), str(user_id).encode(), hashlib.sha256).hexdigest()
    return raw[:16]

def tg_api(method, **params):
    url = f"{TELEGRAM_API}/{method}"
    resp = requests.post(url, json=params, timeout=15)
    if not resp.ok:
        log.warning("TG API error %s -> %s %s", method, resp.status_code, resp.text)
    return resp

def tg_send_message(chat_id, text, parse_mode=None):
    tg_api("sendMessage", chat_id=chat_id, text=text, parse_mode=parse_mode)

# ---------- وبهوک ----------
@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    message = update.get("message")
    if not message:
        return jsonify({"ok": True})

    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = (message.get("text") or "").strip()

    # ✅ واکنش به همه‌ی حالت‌های start
    if text and text.lower().split()[0].startswith("/start"):
        tg_send_message(chat_id, "سلام 😊 لطفاً پیام ناشناست رو بنویس.")
        return jsonify({"ok": True})

    # ✅ دریافت سایر پیام‌ها
    anon_id = make_anon_id(user_id)
    data = load_data()
    entry = {
        "anon_id": anon_id,
        "message": text,
        "timestamp_utc": utc_now_iso()
    }
    data["messages"].append(entry)
    save_data(data)

    # ارسال برای ادمین
    owner_text = (
        f"📨 پیام ناشناس جدید\n\n"
        f"ID: `{anon_id}`\n"
        f"زمان: {entry['timestamp_utc']}\n\n"
        f"{text}"
    )
    tg_send_message(OWNER_CHAT_ID, owner_text, parse_mode="Markdown")

    # پاسخ به کاربر
    tg_send_message(chat_id, "✅ پیامت با موفقیت ارسال شد.")
    return jsonify({"ok": True})

# ---------- ست وبهوک ----------
if __name__ == "__main__":
    ensure_json()
    try:
        r = requests.get(f"{TELEGRAM_API}/setWebhook", params={"url": WEBHOOK_URL}, timeout=15)
        print("Webhook setup:", r.text)
    except Exception as e:
        print("Webhook setup failed:", e)
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)

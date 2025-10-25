# app.py
import os
import json
import hmac
import hashlib
import logging
import base64
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify
import requests

# ---------- تنظیمات ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_CHAT_ID = os.environ.get("OWNER_CHAT_ID", "").strip()
BASE_URL = os.environ.get("BASE_URL", "").strip()
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
ANON_SALT = os.environ.get("ANON_SALT", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()

# ✅ نام کاربری و ریپو گیت‌هاب (حتماً دقیق بنویس)
GITHUB_USER = "mahdikaiser"
REPO = "anonbot"

JSON_PATH = Path("messages.json")

if not BOT_TOKEN or not OWNER_CHAT_ID or not BASE_URL:
    raise RuntimeError("❌ لطفاً BOT_TOKEN, OWNER_CHAT_ID, BASE_URL را در env تنظیم کنید.")

# ---------- تنظیمات عمومی ----------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("anonbot")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
WEBHOOK_URL = f"{BASE_URL}/webhook/{WEBHOOK_SECRET}"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_USER}/{REPO}/contents/messages.json"

app = Flask(__name__)

# ---------- توابع ----------
def utc_now_iso():
    return datetime.utcnow().isoformat() + "Z"

def make_anon_id(uid: int) -> str:
    raw = hmac.new(ANON_SALT.encode(), str(uid).encode(), hashlib.sha256).hexdigest()
    return raw[:16]

def tg_api(method, **params):
    url = f"{TELEGRAM_API}/{method}"
    resp = requests.post(url, json=params, timeout=15)
    if not resp.ok:
        log.warning("TG API error %s -> %s %s", method, resp.status_code, resp.text)
    return resp

def tg_send_message(chat_id, text, parse_mode=None):
    tg_api("sendMessage", chat_id=chat_id, text=text, parse_mode=parse_mode)

# ---------- ذخیره در GitHub ----------
def push_json_to_github(data):
    if not GITHUB_TOKEN:
        log.warning("⛔ GITHUB_TOKEN not set, skipping GitHub upload")
        return

    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    # گرفتن SHA برای به‌روزرسانی فایل
    response = requests.get(GITHUB_API, headers=headers)
    sha = None
    if response.status_code == 200:
        sha = response.json().get("sha")

    content = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode()).decode()
    payload = {
        "message": "auto update messages.json",
        "content": content,
        "sha": sha
    }
    r = requests.put(GITHUB_API, headers=headers, json=payload)
    log.info("📤 GitHub upload status: %s %s", r.status_code, r.text[:120])

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

    # ✅ پاسخ به /start در همه حالت‌ها
    if text and text.lower().split()[0].startswith("/start"):
        tg_send_message(chat_id, "سلام 😊 لطفاً پیام ناشناست رو بنویس.")
        return jsonify({"ok": True})

    # ✅ ذخیره پیام ناشناس
    anon_id = make_anon_id(user_id)
    entry = {
        "anon_id": anon_id,
        "message": text,
        "timestamp_utc": utc_now_iso()
    }

    # ذخیره موقتی در حافظه
    messages = []
    if JSON_PATH.exists():
        messages = json.loads(JSON_PATH.read_text(encoding="utf-8")).get("messages", [])
    messages.append(entry)
    data = {"messages": messages}
    JSON_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # آپلود در GitHub
    push_json_to_github(data)

    # ارسال به ادمین
    owner_text = f"📨 پیام ناشناس جدید\n\nID: `{anon_id}`\nزمان: {entry['timestamp_utc']}\n\n{text}"
    tg_send_message(OWNER_CHAT_ID, owner_text, parse_mode="Markdown")

    # پاسخ به فرستنده
    tg_send_message(chat_id, "✅ پیامت با موفقیت ارسال شد.")
    return jsonify({"ok": True})

# ---------- راه‌اندازی ----------
if __name__ == "__main__":
    try:
        r = requests.get(f"{TELEGRAM_API}/setWebhook", params={"url": WEBHOOK_URL}, timeout=15)
        print("Webhook setup:", r.text)
    except Exception as e:
        print("Webhook setup failed:", e)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

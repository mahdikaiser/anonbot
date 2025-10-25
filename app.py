# app.py
import os
import json
import hmac
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, send_file
import requests

# ---------- تنظیمات ENV ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_CHAT_ID = os.environ.get("OWNER_CHAT_ID", "").strip()
BASE_URL = os.environ.get("BASE_URL", "").strip()
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
ANON_SALT = os.environ.get("ANON_SALT", "")
CHANNEL_TO_CHECK = os.environ.get("CHANNEL_TO_CHECK", "").strip()
JSON_PATH = Path("messages.json")

if not BOT_TOKEN or not OWNER_CHAT_ID or not BASE_URL:
    raise RuntimeError("❌ لطفاً BOT_TOKEN, OWNER_CHAT_ID, BASE_URL را در env تنظیم کنید.")

# ---------- تنظیمات عمومی ----------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("anonbot")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
WEBHOOK_URL = f"{BASE_URL}/webhook/{WEBHOOK_SECRET}"

app = Flask(__name__)

def ensure_json():
    if not JSON_PATH.exists():
        JSON_PATH.write_text(json.dumps({"messages": [], "last_online": None}, ensure_ascii=False, indent=2), encoding="utf-8")

def load_data():
    ensure_json()
    return json.loads(JSON_PATH.read_text(encoding="utf-8"))

def save_data(data):
    JSON_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def utc_now_iso():
    return datetime.utcnow().isoformat() + "Z"

def make_anon_id(real_user_id: int) -> str:
    raw = hmac.new(ANON_SALT.encode(), str(real_user_id).encode(), hashlib.sha256).hexdigest()
    return raw[:16]

def tg_api(method: str, **params):
    url = f"{TELEGRAM_API}/{method}"
    resp = requests.post(url, json=params, timeout=15)
    if not resp.ok:
        log.warning("TG API error %s -> %s %s", method, resp.status_code, resp.text)
    return resp

def tg_send_message(chat_id, text, parse_mode=None):
    return tg_api("sendMessage", chat_id=chat_id, text=text, parse_mode=parse_mode)

def tg_send_document(chat_id, filepath):
    url = f"{TELEGRAM_API}/sendDocument"
    with open(filepath, "rb") as f:
        files = {"document": (os.path.basename(filepath), f)}
        data = {"chat_id": chat_id}
        return requests.post(url, files=files, data=data, timeout=30)

def check_channel_membership(user_id) -> bool:
    if not CHANNEL_TO_CHECK:
        return True
    try:
        resp = requests.get(
            f"{TELEGRAM_API}/getChatMember",
            params={"chat_id": CHANNEL_TO_CHECK, "user_id": user_id},
            timeout=15
        )
        j = resp.json()
        if not j.get("ok"):
            log.warning("getChatMember failed: %s", j)
            return True
        status = j["result"]["status"]
        return status in ("member", "administrator", "creator")
    except Exception as e:
        log.warning("check_channel_membership error: %s", e)
        return True

def set_webhook():
    try:
        r = requests.get(f"{TELEGRAM_API}/setWebhook", params={"url": WEBHOOK_URL}, timeout=15)
        log.info("setWebhook -> %s %s", r.status_code, r.text)
    except Exception as e:
        log.error("setWebhook error: %s", e)

@app.route("/", methods=["GET"])
def root():
    return jsonify({"ok": True, "name": "AnonBot", "time": utc_now_iso()})

@app.route("/get_messages", methods=["GET"])
def get_messages():
    ensure_json()
    return send_file(JSON_PATH, mimetype="application/json", as_attachment=True, download_name="messages.json")

@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    message = update.get("message") or update.get("edited_message")
    if not message:
        return jsonify({"ok": True})

    chat = message.get("chat", {})
    from_user = message.get("from", {}) or {}
    chat_id = chat.get("id")
    user_id = from_user.get("id")
    text = (message.get("text") or "").strip()

    # ✅ پاسخ به /start یا هر حالت مشابه
    if text.lower().startswith("/start"):
        if check_channel_membership(user_id):
            tg_send_message(chat_id, "سلام 😊 لطفاً پیام ناشناست رو بنویس.")
        else:
            tg_send_message(chat_id, f"❗️لطفاً اول عضو کانال {CHANNEL_TO_CHECK} شو و بعد دوباره /start بزن.")
        return jsonify({"ok": True})

    # فرمان‌های ادمین
    if text.startswith("/online") and str(user_id) == OWNER_CHAT_ID:
        data = load_data()
        data["last_online"] = utc_now_iso()
        save_data(data)
        tg_send_message(chat_id, "وضعیت آنلاین ثبت شد ✅")
        return jsonify({"ok": True})

    if text.startswith("/dump") and str(user_id) == OWNER_CHAT_ID:
        ensure_json()
        tg_send_message(chat_id, f"تعداد پیام‌ها: {len(load_data().get('messages', []))}")
        tg_send_document(chat_id, JSON_PATH)
        return jsonify({"ok": True})

    # ذخیره پیام ناشناس
    anon_id = make_anon_id(user_id)
    content_preview = text or message.get("caption") or "<Non-text message>"

    entry = {
        "anon_id": anon_id,
        "message": content_preview,
        "timestamp_utc": utc_now_iso(),
        "chat_type": chat.get("type"),
    }
    data = load_data()
    data["messages"].append(entry)
    save_data(data)

    # ارسال به ادمین
    owner_text = f"📨 پیام ناشناس جدید\n\nID: `{anon_id}`\nزمان (UTC): {entry['timestamp_utc']}\n\n{content_preview}"
    tg_send_message(OWNER_CHAT_ID, owner_text, parse_mode="Markdown")

    # پاسخ به فرستنده
    tg_send_message(chat_id, "✅ پیامت با موفقیت ارسال شد.")

    return jsonify({"ok": True})

if __name__ == "__main__":
    ensure_json()
    set_webhook()
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)

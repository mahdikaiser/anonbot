# app.py
import os
import json
import hmac
import hashlib
import logging
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify, send_file, abort
import requests

# ---------- تنظیمات از طریق متغیر محیطی (روی Render/محلی) ----------
BOT_TOKEN         = os.environ.get("BOT_TOKEN", "").strip()                 # توکن ربات
OWNER_CHAT_ID     = int(os.environ.get("OWNER_CHAT_ID", "0"))               # عددی
CHANNEL_TO_CHECK  = os.environ.get("CHANNEL_TO_CHECK", "").strip()          # مثل @my_channel یا -100...
BASE_URL          = os.environ.get("BASE_URL", "").strip()                  # آدرس عمومی سرویس؛ مثل https://your-app.onrender.com
WEBHOOK_SECRET    = os.environ.get("WEBHOOK_SECRET", "change_this_secret")   # برای امنیت مسیر وبهوک
ANON_SALT         = os.environ.get("ANON_SALT", "strong_salt_v1")           # نمک برای ناشناس‌سازی
JSON_PATH         = Path(os.environ.get("JSON_PATH", "messages.json"))      # مسیر فایل جیسون

if not BOT_TOKEN or not OWNER_CHAT_ID or not BASE_URL:
    raise RuntimeError("لطفاً BOT_TOKEN, OWNER_CHAT_ID, BASE_URL را در env تنظیم کنید.")

# ---------- تنظیمات عمومی ----------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("anonbot")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
WEBHOOK_URL  = f"{BASE_URL}/webhook/{WEBHOOK_SECRET}"

app = Flask(__name__)


# ---------- توابع کمکی ----------
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
    # ناشناس‌سازی با HMAC و نمک
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
    # اگر کانال تنظیم نشده بود، عبور بده
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
            # اگر نتونستیم چک کنیم (کانال پرایوت/دسترسی نداشت)، اجازه می‌دیم عبور کنه
            log.warning("getChatMember failed: %s", j)
            return True
        status = j["result"]["status"]
        return status in ("member", "administrator", "creator")
    except Exception as e:
        log.warning("check_channel_membership error: %s", e)
        return True


# ---------- راه‌اندازی وبهوک در استارتاپ ----------
def set_webhook():
    try:
        r = requests.get(f"{TELEGRAM_API}/setWebhook", params={"url": WEBHOOK_URL}, timeout=15)
        log.info("setWebhook -> %s %s", r.status_code, r.text)
    except Exception as e:
        log.error("setWebhook error: %s", e)


# ---------- روت‌ها ----------
@app.route("/", methods=["GET"])
def root():
    return jsonify({"ok": True, "name": "AnonBot", "time": utc_now_iso()})

@app.route("/health", methods=["GET"])
def health():
    return "OK", 200

@app.route("/get_messages", methods=["GET"])
def get_messages():
    ensure_json()
    # امکان دانلود مستقیم فایل JSON
    return send_file(JSON_PATH, mimetype="application/json", as_attachment=True, download_name="messages.json")

@app.route("/webhook/<secret>", methods=["POST"])
def webhook(secret):
    if secret != WEBHOOK_SECRET:
        abort(403)

    update = request.get_json(force=True, silent=True) or {}
    log.debug("update: %s", update)

    # فقط messageها
    message = update.get("message") or update.get("edited_message")
    if not message:
        return jsonify({"ok": True})

    chat = message.get("chat", {})
    from_user = message.get("from", {}) or {}
    chat_id = chat.get("id")
    user_id = from_user.get("id")

    text = (message.get("text") or "").strip()

    # دستورات
    if text.startswith("/start"):
        if check_channel_membership(user_id):
            tg_send_message(chat_id, "سلام — خوش اومدی! حالا حرفت رو بزن و اینجا بفرست. (پیام به‌صورت ناشناس ارسال می‌شود)")
        else:
            tg_send_message(
                chat_id,
                f"برای ارسال پیام لازمه عضو کانال {CHANNEL_TO_CHECK} باشی.\nبعد از عضویت، دوباره /start رو بزن."
            )
        return jsonify({"ok": True})

    if text.startswith("/online"):
        if user_id != OWNER_CHAT_ID:
            tg_send_message(chat_id, "این دستور فقط برای صاحب ربات است.")
            return jsonify({"ok": True})
        data = load_data()
        data["last_online"] = utc_now_iso()
        save_data(data)
        tg_send_message(chat_id, "وضعیت آنلاین ثبت شد و فایل JSON آپدیت شد.")
        return jsonify({"ok": True})

    if text.startswith("/dump"):
        if user_id != OWNER_CHAT_ID:
            tg_send_message(chat_id, "این دستور فقط برای صاحب ربات است.")
            return jsonify({"ok": True})
        ensure_json()
        tg_send_message(chat_id, f"تعداد پیام‌های ثبت‌شده: {len(load_data().get('messages', []))}")
        tg_send_document(OWNER_CHAT_ID, JSON_PATH)
        return jsonify({"ok": True})

    # پیام عادی کاربر → ذخیره ناشناس + ارسال به مالک
    if text or message.get("caption") or message.get("sticker") or message.get("photo") or message.get("voice") or message.get("document"):
        # ناشناس‌سازی
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

        # ارسال برای مالک (فقط متن/پیش‌نمایش)
        owner_text = f"📨 پیام ناشناس جدید\n\nID: `{anon_id}`\nزمان (UTC): {entry['timestamp_utc']}\n\n{content_preview}"
        tg_send_message(OWNER_CHAT_ID, owner_text, parse_mode="Markdown")

        # تأیید به فرستنده
        tg_send_message(chat_id, "پیامت ثبت و ناشناس ارسال شد. ممنون!")
        return jsonify({"ok": True})

    # سایر موارد
    tg_send_message(chat_id, "متوجه نشدم — از /start استفاده کن.")
    return jsonify({"ok": True})


# ---------- اجرای برنامه ----------
if __name__ == "__main__":
    ensure_json()
    set_webhook()
    # اجرای Flask dev server (برای production روی Render از gunicorn استفاده می‌کنیم)
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)

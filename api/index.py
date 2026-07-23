import logging
import os

import httpx
from flask import Flask, request
from google import genai
from google.genai import types

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = os.getenv("TELEGRAM_OWNER_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-flash-latest"
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
MAX_HISTORY_TURNS = 20

SYSTEM_INSTRUCTION = (
    "Bạn là trợ lý AI cá nhân của Hiên, giao tiếp qua Telegram. "
    "Trả lời bằng tiếng Việt, ngắn gọn, thân thiện và đi thẳng vào trọng tâm."
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# Bộ nhớ tạm trong tiến trình — chỉ tồn tại khi container còn "ấm", không đảm bảo
# bền vững giữa các lần gọi trên môi trường serverless.
chat_histories: dict[int, list[types.Content]] = {}


def send_message(chat_id: int, text: str) -> None:
    text = text or "(không có nội dung)"
    for i in range(0, len(text), 3500):
        httpx.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text[i : i + 3500]},
            timeout=20,
        )


@app.route("/api/webhook", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    message = update.get("message") or update.get("edited_message")
    if not message or "text" not in message:
        return "OK"

    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message["text"].strip()

    if text.startswith("/start"):
        send_message(chat_id, f"Xin chào! Bot đang chạy 24/7 trên Vercel.\nID Telegram của bạn: {user_id}")
        return "OK"

    if text.startswith("/help"):
        send_message(
            chat_id,
            "Bản Vercel này chỉ hỗ trợ chat AI 24/7.\n"
            "/reset - xoá bộ nhớ hội thoại\n\n"
            "Lệnh /run (chạy lệnh trên máy) chỉ khả dụng khi Hiên chạy bot tại máy local, "
            "không hoạt động trên bản Vercel vì lý do an toàn.",
        )
        return "OK"

    if text.startswith("/run"):
        send_message(
            chat_id,
            "Lệnh /run không khả dụng trên bản Vercel (server đám mây, không phải máy của Hiên). "
            "Hãy chạy bot.py tại máy local để dùng /run.",
        )
        return "OK"

    if text.startswith("/reset"):
        chat_histories.pop(chat_id, None)
        send_message(chat_id, "Đã xoá bộ nhớ hội thoại.")
        return "OK"

    if not gemini_client:
        send_message(chat_id, "Chưa cấu hình GEMINI_API_KEY trên Vercel.")
        return "OK"

    history = chat_histories.setdefault(chat_id, [])
    history.append(types.Content(role="user", parts=[types.Part(text=text)]))

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=history,
            config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION),
        )
        reply_text = response.text or "(AI không trả về nội dung)"
    except Exception as exc:
        logger.exception("Gemini API error")
        history.pop()
        send_message(chat_id, f"Lỗi khi gọi AI: {exc}")
        return "OK"

    history.append(types.Content(role="model", parts=[types.Part(text=reply_text)]))
    if len(history) > MAX_HISTORY_TURNS * 2:
        del history[: len(history) - MAX_HISTORY_TURNS * 2]

    send_message(chat_id, reply_text)
    return "OK"

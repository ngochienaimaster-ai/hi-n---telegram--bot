import logging
import os
import subprocess

from dotenv import load_dotenv
from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = os.getenv("TELEGRAM_OWNER_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-flash-latest"
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_HISTORY_TURNS = 20

SYSTEM_INSTRUCTION = (
    "Bạn là trợ lý AI cá nhân của Hiên, giao tiếp qua Telegram. "
    "Trả lời bằng tiếng Việt, ngắn gọn, thân thiện và đi thẳng vào trọng tâm. "
    "Nếu Hiên hỏi về máy tính/project của họ, hãy nhắc rằng có thể dùng lệnh /run để thực thi lệnh trên máy."
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
chat_histories: dict[int, list[types.Content]] = {}


def is_owner(update: Update) -> bool:
    return OWNER_ID is not None and str(update.effective_user.id) == str(OWNER_ID)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"Xin chào {update.effective_user.first_name}! Gõ /help để xem các lệnh.\n"
        f"ID Telegram của bạn là: {update.effective_user.id}"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Các lệnh:\n"
        "/start - Bắt đầu\n"
        "/help - Trợ giúp\n"
        "/run <lệnh> - Chạy lệnh shell trên máy (chỉ chủ bot)\n"
        "/reset - Xoá bộ nhớ hội thoại với AI\n\n"
        "Gửi bất kỳ tin nhắn nào khác để trò chuyện với AI (Gemini)."
    )


async def run_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        await update.message.reply_text("Bạn không có quyền dùng lệnh này.")
        logger.warning("Unauthorized /run attempt by user_id=%s", update.effective_user.id)
        return

    cmd = " ".join(context.args)
    if not cmd:
        await update.message.reply_text("Cú pháp: /run <lệnh>")
        return

    await update.message.reply_text(f"Đang chạy: {cmd}")
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = (result.stdout or "") + (result.stderr or "")
        output = output.strip() or "(không có output)"
    except subprocess.TimeoutExpired:
        output = "Lệnh chạy quá 60 giây, đã bị huỷ."

    for i in range(0, len(output), 3500):
        await update.message.reply_text(f"```\n{output[i:i + 3500]}\n```")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_histories.pop(update.effective_chat.id, None)
    await update.message.reply_text("Đã xoá bộ nhớ hội thoại.")


async def chat_with_ai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not gemini_client:
        await update.message.reply_text(
            "Chưa cấu hình GEMINI_API_KEY trong .env nên AI chưa hoạt động. "
            "Bot chỉ echo lại tin nhắn:\n" + update.message.text
        )
        return

    chat_id = update.effective_chat.id
    history = chat_histories.setdefault(chat_id, [])
    history.append(types.Content(role="user", parts=[types.Part(text=update.message.text)]))

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
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
        await update.message.reply_text(f"Lỗi khi gọi AI: {exc}")
        return

    history.append(types.Content(role="model", parts=[types.Part(text=reply_text)]))
    if len(history) > MAX_HISTORY_TURNS * 2:
        del history[: len(history) - MAX_HISTORY_TURNS * 2]

    await update.message.reply_text(reply_text)


def main() -> None:
    if not TOKEN:
        raise RuntimeError("Thiếu TELEGRAM_BOT_TOKEN. Hãy tạo file .env dựa trên .env.example")
    if not OWNER_ID:
        logger.warning("Chưa đặt TELEGRAM_OWNER_ID trong .env — /run sẽ bị chặn với mọi người.")
    if not GEMINI_API_KEY:
        logger.warning("Chưa đặt GEMINI_API_KEY trong .env — bot sẽ chỉ echo lại tin nhắn.")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("run", run_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_with_ai))

    logger.info("Bot đang chạy...")
    app.run_polling()


if __name__ == "__main__":
    main()

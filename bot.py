"""
YouTube Clipper Telegram Bot
Runs on Railway, deployed via GitHub
"""

import os
import sys
import logging
import asyncio
import re
from pathlib import Path

# Load .env file for local development (no-op if file doesn't exist)
from dotenv import load_dotenv
load_dotenv()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

from clipper import YouTubeClipper, get_cookie_status

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
def _require_env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        logger.error(
            f"❌ Environment variable '{key}' is not set!\n"
            "  → Local dev: copy .env.example to .env and fill in values\n"
            "  → Railway: add it under your service Variables tab"
        )
        sys.exit(1)
    return val

BOT_TOKEN         = _require_env("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY = _require_env("ANTHROPIC_API_KEY")
MAX_FILE_SIZE_MB  = int(os.environ.get("MAX_FILE_SIZE_MB", "50"))

YOUTUBE_PATTERN = re.compile(
    r"(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[\w\-]+"
)

# ── User session state ─────────────────────────────────────────────────────────
sessions: dict = {}


# ── Helpers ───────────────────────────────────────────────────────────────────
def extract_youtube_url(text: str) -> str | None:
    match = YOUTUBE_PATTERN.search(text)
    return match.group(0) if match else None


def chapters_keyboard(chapters: list, selected: set) -> InlineKeyboardMarkup:
    buttons = []
    for i, ch in enumerate(chapters):
        check = "✅" if i in selected else "⬜"
        label = f"{check} {ch['title'][:40]}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"toggle_{i}")])

    buttons.append([
        InlineKeyboardButton("🎬 Clip Selected", callback_data="clip_selected"),
        InlineKeyboardButton("✅ Select All",    callback_data="select_all"),
    ])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(buttons)


# ── Handlers ──────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *YouTube Clipper Bot*\n\n"
        "Kirim link YouTube dan saya akan:\n"
        "1️⃣ Download video\n"
        "2️⃣ Analisis isi dengan AI → bagi jadi chapter semantik\n"
        "3️⃣ Kamu pilih chapter mana yang mau di-clip\n"
        "4️⃣ Kirim video clip (maks. 5 menit, sudah dikompresi) + subtitle bilingual (EN+ID)\n\n"
        "Cukup kirim link YouTube sekarang! 🚀",
        parse_mode=ParseMode.MARKDOWN,
    )


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Cara pakai:*\n\n"
        "• Kirim link YouTube (youtube.com atau youtu.be)\n"
        "• Tunggu AI analisis chapter (~30 detik)\n"
        "• Pilih chapter yang mau di-clip\n"
        "• Tekan *Clip Selected*\n"
        "• Terima file video + SRT subtitle\n\n"
        "*Catatan:*\n"
        "• Setiap clip dibatasi maks. 5 menit\n"
        "• Video otomatis dikompres agar file kecil\n"
        "• Jika subtitle YouTube tidak tersedia, chapter dibagi otomatis per 3 menit\n\n"
        "*Perintah:*\n"
        "/start — Mulai\n"
        "/help  — Bantuan\n"
        "/cancel — Batalkan proses saat ini\n"
        "/cookiestatus — Cek status cookies YouTube",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cookiestatus_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    status = get_cookie_status()
    icon = "✅" if status["ok"] else "❌"
    source = status.get("source") or "—"
    detail = status.get("detail", "")
    await update.message.reply_text(
        f"{icon} *Status Cookies YouTube*\n\n"
        f"*Sumber:* `{source}`\n"
        f"*Detail:* {detail}\n\n"
        + (
            "Cookies aktif — YouTube tidak akan blokir bot."
            if status["ok"] else
            "⚠️ Cookies bermasalah!\n\n"
            "*Cara fix:*\n"
            "1. Export cookies dari browser (login YouTube dulu)\n"
            "2. Jalankan: `python export_cookies.py cookies.txt`\n"
            "3. Copy output ke Railway Variables sebagai `YOUTUBE_COOKIES`\n"
            "4. Atau paste langsung isi `cookies.txt` ke variable (tanpa encode)"
        ),
        parse_mode=ParseMode.MARKDOWN,
    )


async def cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    sessions.pop(chat_id, None)
    await update.message.reply_text("❌ Proses dibatalkan.")


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text or ""

    url = extract_youtube_url(text)
    if not url:
        await update.message.reply_text(
            "⚠️ Link YouTube tidak ditemukan.\nContoh: https://youtu.be/dQw4w9WgXcQ"
        )
        return

    msg = await update.message.reply_text("⏳ Mengambil info video…")

    clipper = YouTubeClipper(
        anthropic_api_key=ANTHROPIC_API_KEY,
        output_dir=f"/tmp/yt-clips/{chat_id}",
    )
    sessions[chat_id] = {"url": url, "clipper": clipper, "selected": set()}

    try:
        info = await asyncio.get_event_loop().run_in_executor(
            None, clipper.fetch_info, url
        )

        await msg.edit_text(
            f"🤖 AI sedang analisis chapter untuk:\n*{info['title'][:60]}*\n\n"
            "Mengambil subtitle + analisis konten…\n"
            "_(Jika subtitle YouTube rate-limited, akan pakai pembagian otomatis)_",
            parse_mode=ParseMode.MARKDOWN,
        )

        # generate_chapters now returns (chapters, used_ai)
        chapters, used_ai = await asyncio.get_event_loop().run_in_executor(
            None, clipper.generate_chapters, url
        )

        sessions[chat_id]["chapters"] = chapters
        sessions[chat_id]["info"] = info

        chapter_list = "\n".join(
            f"{i+1}. [{ch['start']} → {ch['end']}] {ch['title']}"
            for i, ch in enumerate(chapters)
        )

        mode_note = (
            "🤖 _Chapter dibuat oleh AI berdasarkan isi video_"
            if used_ai else
            "⏱ _Subtitle tidak tersedia — chapter dibagi otomatis per 3 menit_"
        )

        await msg.edit_text(
            f"✅ Ditemukan *{len(chapters)} chapter* untuk:\n"
            f"*{info['title'][:60]}*\n\n"
            f"```\n{chapter_list}\n```\n\n"
            f"{mode_note}\n\n"
            "Pilih chapter yang mau di-clip:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=chapters_keyboard(chapters, set()),
        )

    except Exception as e:
        logger.exception("Error during fetch/analyze")
        sessions.pop(chat_id, None)
        await msg.edit_text(
            f"❌ Error: {e}\n\n"
            "💡 Tip: Jika error 429, tunggu beberapa menit lalu coba lagi."
        )


async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    data = query.data

    session = sessions.get(chat_id)
    if not session:
        await query.edit_message_text("⚠️ Sesi tidak ditemukan. Kirim link YouTube lagi.")
        return

    chapters = session.get("chapters", [])
    selected: set = session["selected"]

    # ── Toggle chapter ────────────────────────────────────────────────────────
    if data.startswith("toggle_"):
        idx = int(data.split("_")[1])
        if idx in selected:
            selected.discard(idx)
        else:
            selected.add(idx)
        await query.edit_message_reply_markup(
            reply_markup=chapters_keyboard(chapters, selected)
        )

    # ── Select all ────────────────────────────────────────────────────────────
    elif data == "select_all":
        selected.update(range(len(chapters)))
        await query.edit_message_reply_markup(
            reply_markup=chapters_keyboard(chapters, selected)
        )

    # ── Cancel ────────────────────────────────────────────────────────────────
    elif data == "cancel":
        sessions.pop(chat_id, None)
        await query.edit_message_text("❌ Proses dibatalkan.")

    # ── Clip selected ─────────────────────────────────────────────────────────
    elif data == "clip_selected":
        if not selected:
            await query.answer("⚠️ Pilih minimal 1 chapter!", show_alert=True)
            return

        await query.edit_message_text(
            f"🎬 Memproses {len(selected)} chapter…\n\n"
            "📥 Download video (720p)\n"
            "✂️  Clip (maks. 5 menit per chapter)\n"
            "🗜  Kompresi H.264 → file kecil\n"
            "🌐 Terjemah subtitle EN → ID\n\n"
            "Sabar ya, proses ini butuh beberapa menit ☕"
        )

        clipper: YouTubeClipper = session["clipper"]
        url = session["url"]
        chosen = sorted(selected)

        try:
            results = await asyncio.get_event_loop().run_in_executor(
                None, clipper.process_chapters, url, [chapters[i] for i in chosen]
            )

            success = [r for r in results if not r.get("error")]
            failed  = [r for r in results if r.get("error")]

            summary = f"✅ Selesai! {len(success)} clip berhasil"
            if failed:
                summary += f", {len(failed)} gagal"
            await query.message.reply_text(summary)

            for res in results:
                if res.get("error"):
                    await query.message.reply_text(
                        f"❌ *{res['title']}* gagal diproses:\n`{res['error']}`",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    continue

                dur_s   = res.get("duration_s", 0)
                dur_str = f"{dur_s//60}m{dur_s%60:02d}s"
                size_mb = res.get("size_mb", 0)

                cap = (
                    f"🎬 *{res['title']}*\n"
                    f"⏱ {res['start']} → {res['end']} ({dur_str})\n"
                    f"💾 {size_mb} MB (compressed)\n"
                    f"📝 {res.get('summary', '')}"
                )

                video_path = Path(res["video_path"])
                if video_path.exists():
                    if size_mb <= MAX_FILE_SIZE_MB:
                        with open(video_path, "rb") as f:
                            await query.message.reply_video(
                                f, caption=cap[:1024], parse_mode=ParseMode.MARKDOWN
                            )
                    else:
                        await query.message.reply_text(
                            f"⚠️ *{res['title']}* masih terlalu besar setelah kompresi "
                            f"({size_mb}MB > {MAX_FILE_SIZE_MB}MB limit Telegram).\n"
                            "Coba pilih chapter yang lebih pendek.",
                            parse_mode=ParseMode.MARKDOWN,
                        )

                srt_path = Path(res.get("srt_path", ""))
                if srt_path.exists():
                    with open(srt_path, "rb") as f:
                        await query.message.reply_document(
                            f,
                            filename=srt_path.name,
                            caption=f"📝 Subtitle bilingual EN+ID: {res['title']}",
                        )

            sessions.pop(chat_id, None)

        except Exception as e:
            logger.exception("Clipping error")
            sessions.pop(chat_id, None)
            await query.message.reply_text(
                f"❌ Error saat clipping: {e}\n\n"
                "💡 Jika error 429, tunggu beberapa menit lalu coba lagi."
            )


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("help",   help_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CommandHandler("cookiestatus", cookiestatus_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Bot started — polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

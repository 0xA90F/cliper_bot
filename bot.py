import os
import re
import asyncio
import logging
import tempfile
import shutil
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from clipper import YouTubeClipper

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
MAX_FILE_SIZE_MB = int(os.environ.get("MAX_FILE_SIZE_MB", "50"))

# In-memory session store: {chat_id: {url, chapters, work_dir}}
sessions = {}

YOUTUBE_REGEX = re.compile(
    r"(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[\w\-]+"
)


# ── /start ──────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 *YouTube Clipper Bot*\n\n"
        "Kirim link YouTube dan saya akan:\n"
        "• Download video\n"
        "• Analisis & bagi per segmen ~5 menit\n"
        "• Kirim klip yang kamu pilih\n\n"
        "Ketik /help untuk bantuan.",
        parse_mode="Markdown"
    )


# ── /help ────────────────────────────────────────────────────────────────────
async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Cara Pakai*\n\n"
        "1. Kirim link YouTube (contoh: https://youtu.be/xxx)\n"
        "2. Tunggu bot menganalisis video\n"
        "3. Pilih segmen mana yang mau di-download\n"
        "4. Bot akan kirim file MP4\n\n"
        "⚠️ *Batasan*\n"
        f"• Ukuran file max {MAX_FILE_SIZE_MB} MB per klip\n"
        "• Video max 2 jam\n"
        "• Durasi segmen ~5 menit\n\n"
        "Ketik /cancel untuk membatalkan proses.",
        parse_mode="Markdown"
    )


# ── /cancel ──────────────────────────────────────────────────────────────────
async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = sessions.pop(chat_id, None)
    if session and session.get("work_dir"):
        shutil.rmtree(session["work_dir"], ignore_errors=True)
    await update.message.reply_text("❌ Proses dibatalkan.")


# ── Handle YouTube URL ────────────────────────────────────────────────────────
async def handle_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    if not YOUTUBE_REGEX.search(text):
        await update.message.reply_text(
            "⚠️ Sepertinya itu bukan link YouTube yang valid.\n"
            "Contoh: https://youtu.be/dQw4w9WgXcQ"
        )
        return

    # Clean up old session
    old = sessions.pop(chat_id, None)
    if old and old.get("work_dir"):
        shutil.rmtree(old["work_dir"], ignore_errors=True)

    msg = await update.message.reply_text("⏳ Menganalisis video, harap tunggu...")

    work_dir = tempfile.mkdtemp(prefix="yt_clip_")
    clipper = YouTubeClipper(work_dir=work_dir)

    try:
        await ctx.bot.edit_message_text(
            "📥 Mengambil info & subtitle video...",
            chat_id=chat_id, message_id=msg.message_id
        )
        info = await asyncio.get_event_loop().run_in_executor(
            None, clipper.fetch_info_and_subtitles, text
        )

        await ctx.bot.edit_message_text(
            "🧠 Membagi video menjadi segmen ~5 menit...",
            chat_id=chat_id, message_id=msg.message_id
        )
        chapters = clipper.generate_chapters(info)

        if not chapters:
            await ctx.bot.edit_message_text(
                "❌ Tidak bisa membagi video. Pastikan video memiliki subtitle.",
                chat_id=chat_id, message_id=msg.message_id
            )
            shutil.rmtree(work_dir, ignore_errors=True)
            return

        sessions[chat_id] = {
            "url": text,
            "chapters": chapters,
            "info": info,
            "work_dir": work_dir,
            "clipper": clipper,
        }

        # Build chapter selection keyboard
        title = info.get("title", "Video")[:50]
        duration_str = _fmt_duration(info.get("duration", 0))
        text_out = (
            f"🎬 *{title}*\n"
            f"⏱ Durasi: {duration_str}\n"
            f"📋 {len(chapters)} segmen ditemukan\n\n"
            "Pilih segmen yang ingin diunduh:"
        )

        keyboard = []
        for i, ch in enumerate(chapters):
            label = f"{i+1}. {ch['title'][:30]} ({ch['start_fmt']}–{ch['end_fmt']})"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"clip_{i}")])
        keyboard.append([InlineKeyboardButton("📦 Unduh Semua", callback_data="clip_all")])
        keyboard.append([InlineKeyboardButton("❌ Batal", callback_data="clip_cancel")])

        await ctx.bot.edit_message_text(
            text_out,
            chat_id=chat_id,
            message_id=msg.message_id,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.exception("Error processing URL")
        shutil.rmtree(work_dir, ignore_errors=True)
        sessions.pop(chat_id, None)
        await ctx.bot.edit_message_text(
            f"❌ Gagal memproses video:\n`{str(e)[:200]}`",
            chat_id=chat_id, message_id=msg.message_id,
            parse_mode="Markdown"
        )


# ── Callback: chapter selection ───────────────────────────────────────────────
async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    data = query.data

    if data == "clip_cancel":
        session = sessions.pop(chat_id, None)
        if session and session.get("work_dir"):
            shutil.rmtree(session["work_dir"], ignore_errors=True)
        await query.edit_message_text("❌ Dibatalkan.")
        return

    session = sessions.get(chat_id)
    if not session:
        await query.edit_message_text("⚠️ Sesi kedaluwarsa. Kirim URL lagi.")
        return

    chapters = session["chapters"]
    if data == "clip_all":
        indices = list(range(len(chapters)))
    elif data.startswith("clip_"):
        indices = [int(data.split("_")[1])]
    else:
        return

    await query.edit_message_text(
        f"⬇️ Mengunduh & memotong {len(indices)} segmen... Harap tunggu."
    )

    clipper: YouTubeClipper = session["clipper"]
    url = session["url"]
    work_dir = session["work_dir"]

    # Download full video first
    try:
        status_msg = await ctx.bot.send_message(chat_id, "📥 Mengunduh video penuh...")
        video_path = await asyncio.get_event_loop().run_in_executor(
            None, clipper.download_video, url
        )
        await ctx.bot.edit_message_text(
            "✂️ Memotong segmen...", chat_id=chat_id, message_id=status_msg.message_id
        )
    except Exception as e:
        await ctx.bot.send_message(chat_id, f"❌ Gagal download: `{str(e)[:200]}`", parse_mode="Markdown")
        return

    sent = 0
    for idx in indices:
        ch = chapters[idx]
        try:
            clip_path = await asyncio.get_event_loop().run_in_executor(
                None, clipper.clip_segment, video_path, ch, idx
            )
            file_size_mb = os.path.getsize(clip_path) / (1024 * 1024)

            if file_size_mb > MAX_FILE_SIZE_MB:
                await ctx.bot.send_message(
                    chat_id,
                    f"⚠️ Segmen *{ch['title']}* terlalu besar ({file_size_mb:.1f} MB > {MAX_FILE_SIZE_MB} MB), dilewati.",
                    parse_mode="Markdown"
                )
                continue

            caption = (
                f"🎬 *{ch['title']}*\n"
                f"⏱ {ch['start_fmt']} → {ch['end_fmt']}\n"
                f"📦 {file_size_mb:.1f} MB"
            )
            with open(clip_path, "rb") as f:
                await ctx.bot.send_video(
                    chat_id,
                    video=f,
                    caption=caption,
                    parse_mode="Markdown",
                    supports_streaming=True,
                    read_timeout=300,
                    write_timeout=300
                )
            sent += 1
        except Exception as e:
            logger.exception(f"Error clipping segment {idx}")
            await ctx.bot.send_message(
                chat_id,
                f"❌ Gagal klip segmen *{ch['title']}*: `{str(e)[:150]}`",
                parse_mode="Markdown"
            )

    # Cleanup
    session = sessions.pop(chat_id, None)
    if session and session.get("work_dir"):
        shutil.rmtree(session["work_dir"], ignore_errors=True)

    await ctx.bot.edit_message_text(
        f"✅ Selesai! {sent}/{len(indices)} segmen berhasil dikirim.",
        chat_id=chat_id, message_id=status_msg.message_id
    )


def _fmt_duration(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h}j {m}m {s}d"
    return f"{m}m {s}d"


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_handler(CallbackQueryHandler(handle_callback))
    logger.info("Bot started polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

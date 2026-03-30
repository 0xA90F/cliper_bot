# 🎬 YouTube Clipper — Telegram Bot

Bot Telegram yang meng-clip video YouTube secara cerdas menggunakan AI (Claude).  
Deploy gratis di **Railway** via **GitHub**.

---

## ✨ Fitur

| Fitur | Keterangan |
|-------|-----------|
| 🤖 AI Chapter Analysis | Claude analisis transcript → bagi jadi chapter semantik (bukan potong asal) |
| ✂️ Precise Clipping | FFmpeg clip dengan timing frame-accurate |
| 🌐 Bilingual Subtitles | Terjemah subtitle EN → Indonesia secara batch (hemat API call) |
| 📤 Auto Send | Bot langsung kirim file `.mp4` + `.srt` ke Telegram |

---

## 🚀 Deploy ke Railway (via GitHub)

### Langkah 1 — Buat Bot Telegram

1. Buka Telegram → cari **@BotFather**
2. Ketik `/newbot` → ikuti instruksi
3. Simpan **Bot Token** yang diberikan

### Langkah 2 — Dapatkan Anthropic API Key

1. Buka [console.anthropic.com](https://console.anthropic.com)
2. Buat API Key baru
3. Simpan key-nya

### Langkah 3 — Push ke GitHub

```bash
# Clone / fork repo ini, atau buat repo baru
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/USERNAME/yt-clipper-bot.git
git push -u origin main
```

### Langkah 4 — Deploy di Railway

1. Buka [railway.app](https://railway.app) → Login dengan GitHub
2. Klik **New Project → Deploy from GitHub repo**
3. Pilih repo `yt-clipper-bot`
4. Railway otomatis detect `Dockerfile` dan mulai build

### Langkah 5 — Set Environment Variables di Railway

Di dashboard Railway → tab **Variables**, tambahkan:

| Variable | Value |
|----------|-------|
| `TELEGRAM_BOT_TOKEN` | Token dari BotFather |
| `ANTHROPIC_API_KEY` | API key dari Anthropic |
| `MAX_FILE_SIZE_MB` | `50` (opsional, default 50) |

Setelah disimpan, Railway otomatis restart dan bot langsung aktif! ✅

---

## 💻 Jalankan Lokal (Development)

```bash
# Clone repo
git clone https://github.com/USERNAME/yt-clipper-bot.git
cd yt-clipper-bot

# Setup env
cp .env.example .env
# Edit .env → isi TELEGRAM_BOT_TOKEN dan ANTHROPIC_API_KEY

# Install deps Python
pip install -r requirements.txt

# Install sistem deps (Ubuntu/Debian)
sudo apt install ffmpeg libass-dev

# Jalankan bot
python bot.py
```

---

## 🤖 Cara Pakai Bot

1. Cari bot kamu di Telegram → `/start`
2. Kirim link YouTube:
   ```
   https://youtube.com/watch?v=VIDEO_ID
   ```
3. Tunggu AI analisis chapter (~30 detik)
4. Pilih chapter yang mau di-clip dengan tap tombol ✅
5. Tekan **🎬 Clip Selected**
6. Terima file `.mp4` + `.srt` bilingual di chat!

---

## 📂 Struktur Project

```
yt-clipper-bot/
├── bot.py              # Telegram bot handler & conversation flow
├── clipper.py          # Core logic: download, AI chapters, clip, translate
├── requirements.txt    # Python dependencies
├── Dockerfile          # Container image (Railway pakai ini)
├── railway.toml        # Railway deployment config
├── .env.example        # Template environment variables
└── .gitignore
```

---

## ⚠️ Batasan

- File video ≤ 50MB bisa langsung dikirim via Telegram (bot limit)
- Untuk video lebih besar, bot kirim notifikasi ukuran file
- Subtitle otomatis (auto-generated) kadang kurang akurat untuk bahasa non-English

---

## 🛠️ Troubleshooting

**Bot tidak merespons:**
- Pastikan `TELEGRAM_BOT_TOKEN` benar di Railway Variables
- Cek Railway Logs untuk error

**Error download video:**
- Beberapa video YouTube di-region lock
- Coba set proxy di env: `YT_DLP_PROXY=http://proxy:port`

**Subtitle tidak ter-generate:**
- Tidak semua video YouTube punya subtitle
- Bot akan skip translasi dan tetap kirim clip video

---

## 📝 Lisensi

MIT License — bebas digunakan dan dimodifikasi.

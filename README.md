# 🎬 YouTube Clipper Telegram Bot

Bot Telegram yang otomatis memotong video YouTube menjadi segmen ~5 menit dan mengirimkannya langsung ke chat.

## Fitur

- ✅ Download video YouTube (hingga 720p)
- ✅ Otomatis bagi per segmen ~5 menit (bisa diatur)
- ✅ Pilih segmen mana yang mau diunduh
- ✅ Kirim langsung sebagai file MP4 ke Telegram
- ✅ Mendukung pembagian berdasarkan chapter asli video
- ✅ Fallback ke pembagian waktu jika tidak ada subtitle/chapter

---

## 🚀 Deploy ke Railway via GitHub

### Langkah 1: Buat Bot Telegram

1. Buka Telegram, cari **@BotFather**
2. Kirim `/newbot`
3. Ikuti instruksi, dapatkan **BOT_TOKEN** (format: `1234567890:ABCxyz...`)

---

### Langkah 2: Upload ke GitHub

1. Buat repository baru di [github.com/new](https://github.com/new)
   - Nama: `yt-clipper-bot` (bebas)
   - Visibility: **Private** (direkomendasikan)
   - Klik **Create repository**

2. Upload semua file proyek ini:
   ```
   bot.py
   clipper.py
   requirements.txt
   Dockerfile
   railway.toml
   .gitignore
   .env.example
   README.md
   ```

   **Via GitHub web:**
   - Klik **Add file → Upload files**
   - Drag & drop semua file
   - Klik **Commit changes**

   **Via Git CLI:**
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git remote add origin https://github.com/USERNAME/yt-clipper-bot.git
   git push -u origin main
   ```

---

### Langkah 3: Deploy ke Railway

1. Buka [railway.app](https://railway.app) dan login (bisa pakai akun GitHub)

2. Klik **New Project → Deploy from GitHub repo**

3. Pilih repository `yt-clipper-bot` yang tadi dibuat

4. Railway akan otomatis deteksi Dockerfile

5. **Tambahkan Environment Variable:**
   - Klik tab **Variables**
   - Klik **New Variable**
   - Tambahkan:
     | Key | Value |
     |-----|-------|
     | `BOT_TOKEN` | Token dari BotFather |
     | `TARGET_SEGMENT_DURATION` | `300` (5 menit, opsional) |
     | `MAX_FILE_SIZE_MB` | `50` (opsional) |

6. Klik **Deploy** — Railway akan build & run otomatis

7. Selesai! Bot aktif 24/7 🎉

---

### Langkah 4: Test Bot

Buka Telegram, cari bot kamu, kirim:
```
/start
```
Lalu kirim link YouTube:
```
https://youtu.be/dQw4w9WgXcQ
```

---

## Perintah Bot

| Perintah | Fungsi |
|----------|--------|
| `/start` | Pesan sambutan |
| `/help` | Panduan penggunaan |
| `/cancel` | Batalkan proses aktif |
| *(kirim URL YouTube)* | Mulai proses klip |

---

## Konfigurasi

| Variabel | Default | Keterangan |
|----------|---------|------------|
| `BOT_TOKEN` | — | **Wajib** — token dari BotFather |
| `TARGET_SEGMENT_DURATION` | `300` | Durasi segmen dalam detik (300 = 5 menit) |
| `MAX_FILE_SIZE_MB` | `50` | Batas ukuran file yang dikirim |

---

## Catatan

- Telegram membatasi upload file hingga **50 MB** untuk bot biasa
- Video panjang (>1 jam) membutuhkan waktu lebih lama untuk diproses
- Railway free tier memiliki batas resource — upgrade jika perlu
- Untuk video yang sangat besar, pertimbangkan meningkatkan timeout

---

## Troubleshooting

**Bot tidak merespons?**
- Cek log di Railway dashboard → **Deployments → View Logs**
- Pastikan `BOT_TOKEN` sudah benar di Variables

**"Download gagal"?**
- Video mungkin dibatasi geografis atau privat
- Coba video lain sebagai tes

**File terlalu besar?**
- Kurangi `TARGET_SEGMENT_DURATION` (misal `180` = 3 menit)
- Atau naikkan `MAX_FILE_SIZE_MB` (max 50 untuk Telegram bot)

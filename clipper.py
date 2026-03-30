"""
clipper.py — Core YouTube Clipper logic
Handles: download, AI chapter analysis, FFmpeg clipping, subtitle translation

Cookies: set YOUTUBE_COOKIES env var (base64 Netscape cookies.txt content)
         or place cookies.txt at /app/cookies.txt
"""

import os
import re
import json
import time
import base64
import tempfile
import subprocess
import logging
from pathlib import Path
from typing import Optional

import yt_dlp
import pysrt
import anthropic

logger = logging.getLogger(__name__)

# ── Cookie file resolution ────────────────────────────────────────────────────
_COOKIE_FILE_PATH = Path("/app/cookies.txt")
_TEMP_COOKIE_FILE: Optional[Path] = None


def _get_cookie_file() -> Optional[str]:
    """
    Return path to a Netscape-format cookies.txt file, or None if not configured.
    Priority:
      1. YOUTUBE_COOKIES env var (base64-encoded cookies.txt)
      2. /app/cookies.txt file
    """
    global _TEMP_COOKIE_FILE

    cookies_b64 = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if cookies_b64:
        if _TEMP_COOKIE_FILE and _TEMP_COOKIE_FILE.exists():
            return str(_TEMP_COOKIE_FILE)
        try:
            content = base64.b64decode(cookies_b64).decode("utf-8")
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, prefix="yt_cookies_"
            )
            tmp.write(content)
            tmp.close()
            _TEMP_COOKIE_FILE = Path(tmp.name)
            logger.info(f"Cookies loaded from YOUTUBE_COOKIES env var -> {_TEMP_COOKIE_FILE}")
            return str(_TEMP_COOKIE_FILE)
        except Exception as e:
            logger.error(f"Failed to decode YOUTUBE_COOKIES: {e}")

    if _COOKIE_FILE_PATH.exists():
        logger.info(f"Cookies loaded from {_COOKIE_FILE_PATH}")
        return str(_COOKIE_FILE_PATH)

    return None


def sanitize_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[/\\:*?"<>|]', "", name)
    name = name.replace(" ", "_")
    return name[:max_len]


def _is_bot_detection(err_str: str) -> bool:
    return any(k in err_str for k in ["Sign in to confirm", "not a bot", "bot detection", "cookies"])


def _is_rate_limit(err_str: str) -> bool:
    return "429" in err_str or "Too Many Requests" in err_str


class YouTubeClipper:
    MAX_CLIP_DURATION_SECS = 300  # 5 minutes hard cap per clip

    def __init__(self, anthropic_api_key: str, output_dir: str = "/tmp/yt-clips"):
        self.client = anthropic.Anthropic(api_key=anthropic_api_key)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _base_ydl_opts(self) -> dict:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "sleep_interval": 2,
            "max_sleep_interval": 5,
        }
        cookie_file = _get_cookie_file()
        if cookie_file:
            opts["cookiefile"] = cookie_file
        return opts

    # ── 1. Fetch info ─────────────────────────────────────────────────────────
    def fetch_info(self, url: str) -> dict:
        opts = {**self._base_ydl_opts(), "skip_download": True}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            if _is_bot_detection(str(e)):
                raise RuntimeError(
                    "YouTube mendeteksi bot.\n"
                    "Solusi: tambahkan YOUTUBE_COOKIES ke Railway Variables.\n"
                    "Lihat README bagian 'Setup Cookies'."
                ) from e
            raise
        return {
            "title": info.get("title", "Unknown"),
            "duration": info.get("duration", 0),
            "id": info.get("id", ""),
        }

    # ── 2. Download subtitles ─────────────────────────────────────────────────
    def _download_subtitles(self, url: str, dl_dir: Path) -> Optional[Path]:
        opts = {
            **self._base_ydl_opts(),
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en", "id"],
            "subtitlesformat": "srt",
            "outtmpl": str(dl_dir / "%(title)s.%(ext)s"),
        }
        for attempt in range(3):
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
                break
            except Exception as e:
                err = str(e)
                if _is_bot_detection(err):
                    logger.warning("Subtitle: bot detection — falling back to time-based chapters")
                    return None
                elif _is_rate_limit(err):
                    wait = 15 * (attempt + 1)
                    logger.warning(f"Subtitle 429 — waiting {wait}s (attempt {attempt+1}/3)")
                    time.sleep(wait)
                    if attempt == 2:
                        return None
                else:
                    logger.info(f"Subtitle not available: {e}")
                    return None

        return next(
            dl_dir.glob("*.en.srt"),
            next(dl_dir.glob("*.id.srt"), next(dl_dir.glob("*.srt"), None))
        )

    # ── 3. AI chapter generation ──────────────────────────────────────────────
    def _load_subtitle_text(self, srt_path: Path) -> str:
        subs = pysrt.open(str(srt_path))
        lines = []
        for s in subs:
            ts = f"[{s.start.hours:02d}:{s.start.minutes:02d}:{s.start.seconds:02d}]"
            lines.append(f"{ts} {s.text.replace(chr(10), ' ')}")
        return "\n".join(lines)

    def generate_chapters(self, url: str) -> tuple[list[dict], bool]:
        info = self.fetch_info(url)
        video_id = info["id"]
        duration = info["duration"]
        dl_dir = self.output_dir / video_id
        dl_dir.mkdir(parents=True, exist_ok=True)

        srt_path = self._download_subtitles(url, dl_dir)
        if not srt_path:
            return self._time_based_chapters(duration), False

        subtitle_text = self._load_subtitle_text(srt_path)
        prompt = f"""Analyze this YouTube video transcript and divide it into semantic chapters.
Video duration: {duration} seconds ({duration//60} minutes)
Rules:
- Each chapter should be 2-5 minutes long
- Group content by topic, find natural topic transitions
- No gaps or overlaps between chapters
- Return ONLY valid JSON, no extra text
Transcript:
{subtitle_text[:12000]}
Return JSON array:
[{{"title": "Short title", "start": "HH:MM:SS", "end": "HH:MM:SS", "summary": "1-2 sentences"}}]"""

        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = re.sub(r"```(?:json)?|```", "", response.content[0].text.strip()).strip()
            return json.loads(raw), True
        except Exception as e:
            logger.warning(f"AI chapter analysis failed ({e}) — time-based fallback")
            return self._time_based_chapters(duration), False

    def _time_based_chapters(self, duration: int, chapter_secs: int = 180) -> list[dict]:
        chapters, start = [], 0
        while start < duration:
            end = min(start + chapter_secs, duration)
            chapters.append({
                "title": f"Bagian {len(chapters)+1}",
                "start": self._secs_to_ts(start),
                "end": self._secs_to_ts(end),
                "summary": "",
            })
            start = end
        return chapters

    # ── 4. Download full video ────────────────────────────────────────────────
    def download_video_and_subs(self, url: str, video_id: str) -> tuple[Path, Optional[Path]]:
        dl_dir = self.output_dir / video_id
        dl_dir.mkdir(parents=True, exist_ok=True)
        opts = {
            **self._base_ydl_opts(),
            "format": "bestvideo[height<=720]+bestaudio/best[height<=720]",
            "outtmpl": str(dl_dir / "%(title)s.%(ext)s"),
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en", "id"],
            "subtitlesformat": "srt",
            "merge_output_format": "mp4",
        }
        for attempt in range(3):
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.extract_info(url, download=True)
                break
            except Exception as e:
                err = str(e)
                if _is_bot_detection(err):
                    raise RuntimeError(
                        "YouTube mendeteksi bot saat download video.\n"
                        "Solusi: tambahkan YOUTUBE_COOKIES ke Railway Variables.\n"
                        "Lihat README bagian 'Setup Cookies'."
                    ) from e
                elif _is_rate_limit(err):
                    wait = 15 * (attempt + 1)
                    logger.warning(f"Video download 429 — waiting {wait}s (attempt {attempt+1}/3)")
                    time.sleep(wait)
                    if attempt == 2:
                        raise RuntimeError("YouTube rate limit (429). Coba lagi dalam beberapa menit.") from e
                else:
                    raise

        video_path = next(dl_dir.glob("*.mp4"), None)
        srt_path = next(dl_dir.glob("*.en.srt"), next(dl_dir.glob("*.id.srt"), next(dl_dir.glob("*.srt"), None)))
        if not video_path:
            raise FileNotFoundError("Video download gagal — file mp4 tidak ditemukan.")
        return video_path, srt_path

    def process_chapters(self, url: str, chapters: list[dict]) -> list[dict]:
        info = self.fetch_info(url)
        video_id = info["id"]
        video_path, srt_path = self.download_video_and_subs(url, video_id)
        results = []
        for ch in chapters:
            try:
                results.append(self._process_single_chapter(video_path, srt_path, ch, video_id))
            except Exception as e:
                logger.error(f"Failed chapter '{ch['title']}': {e}")
                results.append({**ch, "error": str(e), "video_path": "", "srt_path": ""})
        return results

    # ── 5. Clip + compress ────────────────────────────────────────────────────
    def _process_single_chapter(self, video_path, srt_path, chapter, video_id):
        safe_title = sanitize_filename(chapter["title"])
        out_dir = self.output_dir / video_id / safe_title
        out_dir.mkdir(parents=True, exist_ok=True)

        start_ms = self._ts_to_ms(chapter["start"])
        end_ms   = self._ts_to_ms(chapter["end"])
        raw_secs = (end_ms - start_ms) // 1000
        trimmed_end = (
            self._secs_to_ts(start_ms // 1000 + self.MAX_CLIP_DURATION_SECS)
            if raw_secs > self.MAX_CLIP_DURATION_SECS
            else chapter["end"]
        )
        actual_secs = min(raw_secs, self.MAX_CLIP_DURATION_SECS)

        raw_clip = out_dir / f"{safe_title}_raw.mp4"
        clip_out = out_dir / f"{safe_title}_clip.mp4"

        subprocess.run([
            "ffmpeg", "-y",
            "-ss", chapter["start"], "-to", trimmed_end,
            "-i", str(video_path), "-c", "copy", str(raw_clip),
        ], check=True, capture_output=True)

        self._compress_video(raw_clip, clip_out)
        raw_clip.unlink(missing_ok=True)

        size_mb = round(clip_out.stat().st_size / 1_000_000, 1)
        result = {
            "title": chapter["title"], "start": chapter["start"], "end": trimmed_end,
            "summary": chapter.get("summary", ""), "video_path": str(clip_out),
            "size_mb": size_mb, "duration_s": actual_secs, "srt_path": "",
        }

        if srt_path and srt_path.exists():
            try:
                bilingual = self._translate_subtitles(
                    srt_path, {**chapter, "end": trimmed_end}, out_dir, safe_title
                )
                result["srt_path"] = str(bilingual)
            except Exception as e:
                logger.warning(f"Subtitle translation failed: {e}")
        return result

    def _compress_video(self, input_path: Path, output_path: Path) -> None:
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-vf", "scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease",
            "-c:v", "libx264", "-crf", "28", "-preset", "fast",
            "-maxrate", "1500k", "-bufsize", "3000k",
            "-c:a", "aac", "-b:a", "96k", "-ac", "2",
            "-movflags", "+faststart", str(output_path),
        ]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0:
            raise RuntimeError(f"Kompresi gagal: {r.stderr.decode()[-300:]}")

    # ── 6. Subtitle translation ───────────────────────────────────────────────
    def _translate_subtitles(self, srt_path, chapter, out_dir, safe_title,
                             target_lang="Bahasa Indonesia", batch_size=20):
        subs = pysrt.open(str(srt_path))
        start_ms = self._ts_to_ms(chapter["start"])
        end_ms   = self._ts_to_ms(chapter["end"])
        chapter_subs = [s for s in subs if s.start.ordinal >= start_ms and s.end.ordinal <= end_ms + 2000]
        if not chapter_subs:
            return srt_path

        translations: dict[int, str] = {}
        for i in range(0, len(chapter_subs), batch_size):
            batch = chapter_subs[i: i + batch_size]
            batch_text = "\n".join(f"[{j}] {s.text.replace(chr(10), ' ')}" for j, s in enumerate(batch, start=i))
            prompt = (
                f"Translate each subtitle line to {target_lang}.\n"
                'Return ONLY JSON: {"0": "terjemahan", "1": "..."}\n'
                "Keep same index numbers. No extra text.\n\n" + batch_text
            )
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514", max_tokens=1000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = re.sub(r"```(?:json)?|```", "", response.content[0].text.strip()).strip()
            translations.update({int(k): v for k, v in json.loads(raw).items()})

        out_srt = out_dir / f"{safe_title}_bilingual.srt"
        with open(out_srt, "w", encoding="utf-8") as f:
            for idx, sub in enumerate(chapter_subs):
                f.write(f"{idx+1}\n{sub.start} --> {sub.end}\n")
                f.write(f"{sub.text.replace(chr(10), ' ')}\n")
                if translations.get(idx):
                    f.write(f"{translations[idx]}\n")
                f.write("\n")
        return out_srt

    # ── Utilities ─────────────────────────────────────────────────────────────
    @staticmethod
    def _secs_to_ts(secs: int) -> str:
        return f"{secs//3600:02d}:{(secs%3600)//60:02d}:{secs%60:02d}"

    @staticmethod
    def _ts_to_ms(ts: str) -> int:
        h, m, s = map(int, ts.split(":"))
        return (h * 3600 + m * 60 + s) * 1000

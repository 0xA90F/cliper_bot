"""
clipper.py — Core YouTube Clipper logic
Handles: download, AI chapter analysis, FFmpeg clipping, subtitle translation
"""

import os
import re
import json
import subprocess
import logging
from pathlib import Path
from typing import Optional

import yt_dlp
import pysrt
import anthropic

logger = logging.getLogger(__name__)


def sanitize_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[/\\:*?"<>|]', "", name)
    name = name.replace(" ", "_")
    return name[:max_len]


class YouTubeClipper:
    def __init__(self, anthropic_api_key: str, output_dir: str = "/tmp/yt-clips"):
        self.client = anthropic.Anthropic(api_key=anthropic_api_key)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Fetch video info + subtitles
    # ─────────────────────────────────────────────────────────────────────────
    def fetch_info(self, url: str) -> dict:
        """Return basic video metadata without downloading the video."""
        ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return {
            "title": info.get("title", "Unknown"),
            "duration": info.get("duration", 0),
            "id": info.get("id", ""),
        }

    def download_video_and_subs(self, url: str, video_id: str) -> tuple[Path, Optional[Path]]:
        """Download video (≤1080p) and English subtitles. Returns (video_path, srt_path)."""
        dl_dir = self.output_dir / video_id
        dl_dir.mkdir(parents=True, exist_ok=True)

        ydl_opts = {
            "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
            "outtmpl": str(dl_dir / "%(title)s.%(ext)s"),
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en"],
            "subtitlesformat": "srt",
            "quiet": True,
            "no_warnings": True,
            "merge_output_format": "mp4",
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = sanitize_filename(info.get("title", video_id))

        # Find downloaded files
        video_path = next(dl_dir.glob("*.mp4"), None)
        srt_path = next(dl_dir.glob("*.en.srt"), next(dl_dir.glob("*.srt"), None))

        if not video_path:
            raise FileNotFoundError("Video download failed — no mp4 found.")

        return video_path, srt_path

    # ─────────────────────────────────────────────────────────────────────────
    # 2. AI chapter generation
    # ─────────────────────────────────────────────────────────────────────────
    def _load_subtitle_text(self, srt_path: Path) -> str:
        """Convert SRT to plain timestamped text for Claude."""
        subs = pysrt.open(str(srt_path))
        lines = []
        for s in subs:
            ts = f"[{s.start.hours:02d}:{s.start.minutes:02d}:{s.start.seconds:02d}]"
            lines.append(f"{ts} {s.text.replace(chr(10), ' ')}")
        return "\n".join(lines)

    def generate_chapters(self, url: str) -> list[dict]:
        """
        Download subtitles and use Claude to generate semantic chapters.
        Returns list of {title, start, end, summary} dicts.
        """
        info = self.fetch_info(url)
        video_id = info["id"]
        duration = info["duration"]

        # Download subs only (no video yet — saves time for chapter analysis)
        dl_dir = self.output_dir / video_id
        dl_dir.mkdir(parents=True, exist_ok=True)

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en"],
            "subtitlesformat": "srt",
            "outtmpl": str(dl_dir / "%(title)s.%(ext)s"),
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        srt_path = next(dl_dir.glob("*.en.srt"), next(dl_dir.glob("*.srt"), None))
        if not srt_path:
            # No subtitles — create time-based fallback chapters
            return self._time_based_chapters(duration)

        subtitle_text = self._load_subtitle_text(srt_path)

        prompt = f"""Analyze this YouTube video transcript and divide it into semantic chapters.

Video duration: {duration} seconds ({duration//60} minutes)

Rules:
- Each chapter should be 2–5 minutes long
- Group content by topic — find natural topic transitions
- No gaps or overlaps between chapters
- Return ONLY valid JSON, no extra text

Transcript:
{subtitle_text[:12000]}

Return JSON array:
[
  {{
    "title": "Short descriptive title",
    "start": "HH:MM:SS",
    "end": "HH:MM:SS",
    "summary": "1-2 sentence summary"
  }}
]"""

        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        # Strip markdown code fences if present
        raw = re.sub(r"```(?:json)?|```", "", raw).strip()

        chapters = json.loads(raw)
        return chapters

    def _time_based_chapters(self, duration: int, chapter_secs: int = 180) -> list[dict]:
        """Fallback: split into equal time chunks."""
        chapters = []
        start = 0
        while start < duration:
            end = min(start + chapter_secs, duration)
            chapters.append({
                "title": f"Chapter {len(chapters)+1}",
                "start": self._secs_to_ts(start),
                "end": self._secs_to_ts(end),
                "summary": "",
            })
            start = end
        return chapters

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Process: download + clip + translate
    # ─────────────────────────────────────────────────────────────────────────
    def process_chapters(self, url: str, chapters: list[dict]) -> list[dict]:
        """Download video then clip + translate each selected chapter."""
        info = self.fetch_info(url)
        video_id = info["id"]

        # Download full video
        video_path, srt_path = self.download_video_and_subs(url, video_id)

        results = []
        for ch in chapters:
            try:
                result = self._process_single_chapter(
                    video_path, srt_path, ch, video_id
                )
                results.append(result)
            except Exception as e:
                logger.error(f"Failed chapter '{ch['title']}': {e}")
                results.append({**ch, "error": str(e)})

        return results

    def _process_single_chapter(
        self, video_path: Path, srt_path: Optional[Path], chapter: dict, video_id: str
    ) -> dict:
        safe_title = sanitize_filename(chapter["title"])
        out_dir = self.output_dir / video_id / safe_title
        out_dir.mkdir(parents=True, exist_ok=True)

        clip_path = out_dir / f"{safe_title}_clip.mp4"

        # ── FFmpeg clip ───────────────────────────────────────────────────────
        cmd = [
            "ffmpeg", "-y",
            "-ss", chapter["start"],
            "-to", chapter["end"],
            "-i", str(video_path),
            "-c", "copy",
            str(clip_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)

        result = {
            "title": chapter["title"],
            "start": chapter["start"],
            "end": chapter["end"],
            "summary": chapter.get("summary", ""),
            "video_path": str(clip_path),
            "srt_path": "",
        }

        # ── Translate subtitles ───────────────────────────────────────────────
        if srt_path and srt_path.exists():
            bilingual_srt = self._translate_subtitles(srt_path, chapter, out_dir, safe_title)
            result["srt_path"] = str(bilingual_srt)

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Subtitle translation (batch, bilingual EN+ID)
    # ─────────────────────────────────────────────────────────────────────────
    def _translate_subtitles(
        self,
        srt_path: Path,
        chapter: dict,
        out_dir: Path,
        safe_title: str,
        target_lang: str = "Bahasa Indonesia",
        batch_size: int = 20,
    ) -> Path:
        subs = pysrt.open(str(srt_path))

        # Filter subs within chapter time range
        start_ms = self._ts_to_ms(chapter["start"])
        end_ms   = self._ts_to_ms(chapter["end"])
        chapter_subs = [
            s for s in subs
            if s.start.ordinal >= start_ms and s.end.ordinal <= end_ms + 2000
        ]

        if not chapter_subs:
            return srt_path  # nothing to translate

        # Batch translate
        translations: dict[int, str] = {}
        for i in range(0, len(chapter_subs), batch_size):
            batch = chapter_subs[i : i + batch_size]
            batch_text = "\n".join(
                f"[{j}] {s.text.replace(chr(10), ' ')}"
                for j, s in enumerate(batch, start=i)
            )

            prompt = (
                f"Translate each subtitle line to {target_lang}.\n"
                "Return ONLY JSON: {{\"0\": \"terjemahan\", \"1\": \"...\"}}\n"
                "Keep same index numbers. No extra text.\n\n"
                + batch_text
            )

            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            raw = re.sub(r"```(?:json)?|```", "", raw).strip()
            batch_translations = json.loads(raw)
            translations.update({int(k): v for k, v in batch_translations.items()})

        # Build bilingual SRT
        out_srt = out_dir / f"{safe_title}_bilingual.srt"
        with open(out_srt, "w", encoding="utf-8") as f:
            for idx, sub in enumerate(chapter_subs):
                translation = translations.get(idx, "")
                f.write(f"{idx + 1}\n")
                f.write(f"{sub.start} --> {sub.end}\n")
                en_text = sub.text.replace("\n", " ")
                f.write(f"{en_text}\n")
                if translation:
                    f.write(f"{translation}\n")
                f.write("\n")

        return out_srt

    # ─────────────────────────────────────────────────────────────────────────
    # Utilities
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _secs_to_ts(secs: int) -> str:
        h = secs // 3600
        m = (secs % 3600) // 60
        s = secs % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    @staticmethod
    def _ts_to_ms(ts: str) -> int:
        parts = ts.split(":")
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        return (h * 3600 + m * 60 + s) * 1000

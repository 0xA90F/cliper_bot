import os
import re
import json
import subprocess
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

TARGET_SEGMENT_DURATION = int(os.environ.get("TARGET_SEGMENT_DURATION", "300"))  # 5 minutes default


class YouTubeClipper:
    def __init__(self, work_dir: str):
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.subtitle_file: Optional[Path] = None

    # ── Fetch video info & subtitles ────────────────────────────────────────
    def fetch_info_and_subtitles(self, url: str) -> Dict[str, Any]:
        """Download video metadata and subtitles (no video yet)."""
        info_file = self.work_dir / "info.json"

        cmd = [
            "yt-dlp",
            "--skip-download",
            "--write-info-json",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs", "en.*,id.*",
            "--sub-format", "vtt/srt/best",
            "--convert-subs", "srt",
            "--no-playlist",
            "-o", str(self.work_dir / "video"),
            url
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            # Try without subtitle restriction
            cmd2 = [
                "yt-dlp",
                "--skip-download",
                "--write-info-json",
                "--no-playlist",
                "-o", str(self.work_dir / "video"),
                url
            ]
            result2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=120)
            if result2.returncode != 0:
                raise RuntimeError(f"yt-dlp gagal: {result2.stderr[-500:]}")

        # Load info.json
        info_files = list(self.work_dir.glob("*.info.json"))
        if not info_files:
            raise RuntimeError("Info video tidak ditemukan setelah download.")
        with open(info_files[0], encoding="utf-8") as f:
            info = json.load(f)

        # Find subtitle file
        srt_files = list(self.work_dir.glob("*.srt"))
        if srt_files:
            self.subtitle_file = srt_files[0]
            logger.info(f"Subtitle found: {self.subtitle_file}")
        else:
            logger.warning("No subtitle file found, will use time-based splitting.")

        return info

    # ── Generate chapters ~5 minutes each ───────────────────────────────────
    def generate_chapters(self, info: Dict[str, Any]) -> List[Dict]:
        duration = info.get("duration", 0)
        if not duration:
            return []

        # 1. Use existing chapters if available and reasonable
        raw_chapters = info.get("chapters") or []
        if raw_chapters and len(raw_chapters) > 1:
            chapters = self._merge_chapters_to_target(raw_chapters, duration)
            if chapters:
                return chapters

        # 2. Use subtitle cues to split naturally
        if self.subtitle_file:
            chapters = self._chapters_from_subtitles(duration)
            if chapters:
                return chapters

        # 3. Fallback: pure time-based splitting
        return self._time_based_chapters(duration)

    def _merge_chapters_to_target(self, raw: List[Dict], total_duration: float) -> List[Dict]:
        """Merge short chapters until each is ~TARGET_SEGMENT_DURATION."""
        result = []
        current_start = None
        current_title = None
        current_end = None

        for ch in raw:
            start = ch.get("start_time", 0)
            end = ch.get("end_time") or total_duration
            title = ch.get("title", f"Bagian {len(result)+1}")

            if current_start is None:
                current_start = start
                current_title = title
                current_end = end
            else:
                if (current_end - current_start) < TARGET_SEGMENT_DURATION:
                    # Extend current
                    current_end = end
                else:
                    result.append(self._make_chapter(current_title, current_start, current_end, len(result)))
                    current_start = start
                    current_title = title
                    current_end = end

        if current_start is not None:
            result.append(self._make_chapter(current_title, current_start, current_end or total_duration, len(result)))

        return result

    def _chapters_from_subtitles(self, total_duration: float) -> List[Dict]:
        """Split by reading subtitle timestamps to find natural break points."""
        cues = self._parse_srt_timestamps(self.subtitle_file)
        if not cues:
            return self._time_based_chapters(total_duration)

        chapters = []
        seg_start = 0.0
        seg_num = 1

        for cue_time in cues:
            if cue_time - seg_start >= TARGET_SEGMENT_DURATION:
                chapters.append(self._make_chapter(
                    f"Segmen {seg_num}", seg_start, cue_time, seg_num - 1
                ))
                seg_start = cue_time
                seg_num += 1

        # Last segment
        if seg_start < total_duration:
            chapters.append(self._make_chapter(
                f"Segmen {seg_num}", seg_start, total_duration, seg_num - 1
            ))

        return chapters

    def _parse_srt_timestamps(self, srt_path: Path) -> List[float]:
        """Extract all subtitle start times from SRT file."""
        times = []
        pattern = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")
        try:
            content = srt_path.read_text(encoding="utf-8", errors="ignore")
            lines = content.split("\n")
            for line in lines:
                if "-->" in line:
                    m = pattern.match(line.strip())
                    if m:
                        h, mn, s, ms = map(int, m.groups())
                        t = h * 3600 + mn * 60 + s + ms / 1000
                        times.append(t)
        except Exception as e:
            logger.warning(f"SRT parse error: {e}")
        return sorted(set(times))

    def _time_based_chapters(self, total_duration: float) -> List[Dict]:
        """Simple time-based splitting every TARGET_SEGMENT_DURATION seconds."""
        chapters = []
        start = 0.0
        num = 1
        while start < total_duration:
            end = min(start + TARGET_SEGMENT_DURATION, total_duration)
            chapters.append(self._make_chapter(f"Segmen {num}", start, end, num - 1))
            start = end
            num += 1
        return chapters

    @staticmethod
    def _make_chapter(title: str, start: float, end: float, idx: int) -> Dict:
        return {
            "idx": idx,
            "title": title,
            "start": start,
            "end": end,
            "start_fmt": _fmt_ts(start),
            "end_fmt": _fmt_ts(end),
            "duration": end - start,
        }

    # ── Download full video ──────────────────────────────────────────────────
    def download_video(self, url: str) -> Path:
        out_template = str(self.work_dir / "full_video.%(ext)s")
        cmd = [
            "yt-dlp",
            "-f", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]/best",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "-o", out_template,
            url
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"Download gagal: {result.stderr[-500:]}")

        videos = list(self.work_dir.glob("full_video.*"))
        if not videos:
            raise RuntimeError("File video tidak ditemukan setelah download.")
        return videos[0]

    # ── Clip a segment ───────────────────────────────────────────────────────
    def clip_segment(self, video_path: Path, chapter: Dict, idx: int) -> Path:
        safe_title = re.sub(r'[^\w\s\-]', '', chapter['title'])[:40].strip().replace(" ", "_")
        out_path = self.work_dir / f"clip_{idx:02d}_{safe_title}.mp4"

        start = chapter["start"]
        duration = chapter["end"] - chapter["start"]

        cmd = [
            "ffmpeg",
            "-y",
            "-ss", str(start),
            "-i", str(video_path),
            "-t", str(duration),
            "-c:v", "libx264",
            "-c:a", "aac",
            "-preset", "fast",
            "-crf", "23",
            "-movflags", "+faststart",
            str(out_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg gagal: {result.stderr[-400:]}")

        return out_path


def _fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h:
        return f"{h:02d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"

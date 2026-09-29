from __future__ import annotations
import os
import re
import shutil
import logging
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import yt_dlp

logger = logging.getLogger("dronnk.media")

class MediaService:
    def __init__(self) -> None:
        self.data_dir = Path(os.getenv("DRONNK_DATA_DIR", "/data"))
        self.audio_dir = self.data_dir / "audio"
        self.video_dir = self.data_dir / "video"
        self.cookies_file = Path(os.getenv("DRONNK_COOKIES_FILE", "/data/cookies.txt"))
        self.max_search = int(os.getenv("DRONNK_MAX_SEARCH_RESULTS", "30"))
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.video_dir.mkdir(parents=True, exist_ok=True)

    def _base_opts(self) -> dict:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "retries": 2,
            "fragment_retries": 2,
            "socket_timeout": 20,
        }
        if self.cookies_file.exists() and self.cookies_file.stat().st_size > 0:
            opts["cookiefile"] = str(self.cookies_file)
        return opts

    @staticmethod
    def _video_id(url: str) -> str:
        try:
            parsed = urlparse(url)
            if "youtu.be" in parsed.netloc:
                return (parsed.path.strip("/") or "unknown").split("/")[0]
            if "youtube.com" in parsed.netloc:
                return parse_qs(parsed.query).get("v", ["unknown"])[0]
        except Exception:
            pass
        return "unknown"

    @staticmethod
    def _safe_title(value: str) -> str:
        value = re.sub(r"[^\w\-. ()\[\]]+", "_", value, flags=re.UNICODE).strip(" ._")
        return (value[:120] or "Dronnk")

    def search(self, query: str, limit: int | None = None) -> list[dict]:
        limit = max(1, min(limit or self.max_search, 50))
        opts = self._base_opts() | {
            "extract_flat": "in_playlist",
            "skip_download": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        result = []
        for item in (info or {}).get("entries") or []:
            if not item or not item.get("id"):
                continue
            vid = str(item["id"])
            thumbs = item.get("thumbnails") or []
            thumb = item.get("thumbnail") or (thumbs[-1].get("url") if thumbs else None)
            result.append({
                "id": vid,
                "title": item.get("title") or "Canción",
                "artist": item.get("channel") or item.get("uploader") or "",
                "thumbnail": thumb,
                "duration": item.get("duration"),
                "source_url": f"https://www.youtube.com/watch?v={vid}",
            })
        return result

    def prepare_audio(self, url: str) -> dict:
        video_id = self._video_id(url)
        existing = next(self.audio_dir.glob(f"{video_id}__*.mp3"), None)
        if existing and existing.stat().st_size > 1024:
            return self._metadata_for_existing(url, existing, video_id, "audio")

        opts = self._base_opts() | {
            "format": "bestaudio/best",
            "outtmpl": str(self.audio_dir / f"{video_id}__%(title).120s.%(ext)s"),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "windowsfilenames": False,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        mp3 = next(self.audio_dir.glob(f"{video_id}__*.mp3"), None)
        if not mp3:
            raise RuntimeError("No se generó el MP3")
        return self._result(info, mp3, video_id)

    def prepare_video(self, url: str) -> dict:
        video_id = self._video_id(url)
        existing = next(self.video_dir.glob(f"{video_id}__*.mp4"), None)
        if existing and existing.stat().st_size > 1024:
            return self._metadata_for_existing(url, existing, video_id, "video")

        opts = self._base_opts() | {
            "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best",
            "merge_output_format": "mp4",
            "outtmpl": str(self.video_dir / f"{video_id}__%(title).120s.%(ext)s"),
            "windowsfilenames": False,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        mp4 = next(self.video_dir.glob(f"{video_id}__*.mp4"), None)
        if not mp4:
            raise RuntimeError("No se generó el MP4")
        return self._result(info, mp4, video_id)

    def _metadata_for_existing(self, url: str, path: Path, video_id: str, kind: str) -> dict:
        opts = self._base_opts() | {"skip_download": True}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception:
            info = {"id": video_id, "title": path.stem.split("__",1)[-1], "webpage_url": url}
        return self._result(info, path, video_id)

    def _result(self, info: dict, path: Path, video_id: str) -> dict:
        return {
            "id": str(info.get("id") or video_id),
            "title": info.get("title") or path.stem,
            "artist": info.get("channel") or info.get("uploader") or "",
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "filename": path.name,
        }

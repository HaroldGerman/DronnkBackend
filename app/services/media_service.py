from __future__ import annotations

import base64
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp

logger = logging.getLogger("dronnk.media")


class MediaTemporarilyUnavailable(RuntimeError):
    """Raised when the upstream provider is temporarily refusing extraction."""


class MediaService:
    def __init__(self) -> None:
        self.data_dir = Path(os.getenv("DRONNK_DATA_DIR", "/data"))
        self.audio_dir = self.data_dir / "audio"
        self.video_dir = self.data_dir / "video"
        self.cookies_file = Path(os.getenv("DRONNK_COOKIES_FILE", "/data/cookies.txt"))
        self.max_search = int(os.getenv("DRONNK_MAX_SEARCH_RESULTS", "30"))
        self.failure_cooldown = int(os.getenv("DRONNK_YOUTUBE_FAILURE_COOLDOWN", "45"))

        # Avoid hammering YouTube repeatedly when the same video is being rejected.
        self._blocked_until: dict[str, float] = {}

        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.video_dir.mkdir(parents=True, exist_ok=True)
        self._bootstrap_cookies()

    def _bootstrap_cookies(self) -> None:
        """
        Optional Railway-friendly cookie bootstrap.

        Supported variables:
          DRONNK_YOUTUBE_COOKIES_B64  -> base64 encoded Netscape cookies.txt
          DRONNK_YOUTUBE_COOKIES      -> raw Netscape cookies.txt

        Nothing is logged except whether a usable file is present.
        """
        raw_b64 = os.getenv("DRONNK_YOUTUBE_COOKIES_B64", "").strip()
        raw_text = os.getenv("DRONNK_YOUTUBE_COOKIES", "").strip()

        try:
            if raw_b64:
                decoded = base64.b64decode(raw_b64).decode("utf-8")
                self.cookies_file.parent.mkdir(parents=True, exist_ok=True)
                self.cookies_file.write_text(decoded, encoding="utf-8")
            elif raw_text:
                self.cookies_file.parent.mkdir(parents=True, exist_ok=True)
                self.cookies_file.write_text(raw_text, encoding="utf-8")
        except Exception:
            logger.exception("could not bootstrap YouTube cookies")

        logger.info(
            "YouTube cookies: %s",
            "configured" if self._has_cookies() else "not configured",
        )

    def _has_cookies(self) -> bool:
        return self.cookies_file.exists() and self.cookies_file.stat().st_size > 32

    def _base_opts(self) -> dict:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "retries": 3,
            "fragment_retries": 3,
            "extractor_retries": 2,
            "socket_timeout": 25,
            "concurrent_fragment_downloads": 1,
            "http_headers": {
                "Accept-Language": "en-US,en;q=0.9",
            },
        }
        if self._has_cookies():
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
        return value[:120] or "Dronnk"

    @staticmethod
    def _looks_like_youtube_block(exc: Exception) -> bool:
        text = str(exc).lower()
        markers = (
            "sign in to confirm you",
            "not a bot",
            "confirm you're not a bot",
            "confirm you’re not a bot",
            "po token",
            "http error 403",
            "requested format is not available",
        )
        return any(marker in text for marker in markers)

    def _assert_not_in_cooldown(self, video_id: str) -> None:
        until = self._blocked_until.get(video_id, 0.0)
        if until > time.time():
            remaining = max(1, int(until - time.time()))
            raise MediaTemporarilyUnavailable(
                f"YouTube rechazó temporalmente este video. Reintenta en {remaining}s."
            )
        self._blocked_until.pop(video_id, None)

    def _mark_blocked(self, video_id: str) -> None:
        self._blocked_until[video_id] = time.time() + self.failure_cooldown

    def _download_profiles(self) -> list[tuple[str, dict]]:
        """
        Try multiple YouTube player clients. Different clients are affected by
        different anti-bot / PO-token enforcement paths.

        web_safari is useful because HLS formats can remain available when
        ordinary web GVS URLs require a PO token. tv/android_vr/web_embedded
        provide additional fallbacks.
        """
        return [
            (
                "web_safari",
                {
                    "extractor_args": {
                        "youtube": {
                            "player_client": ["web_safari"],
                        }
                    }
                },
            ),
            (
                "tv",
                {
                    "extractor_args": {
                        "youtube": {
                            "player_client": ["tv"],
                        }
                    }
                },
            ),
            (
                "android_vr",
                {
                    "extractor_args": {
                        "youtube": {
                            "player_client": ["android_vr"],
                        }
                    }
                },
            ),
            (
                "web_embedded",
                {
                    "extractor_args": {
                        "youtube": {
                            "player_client": ["web_embedded"],
                        }
                    }
                },
            ),
            ("default", {}),
        ]

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
            result.append(
                {
                    "id": vid,
                    "title": item.get("title") or "Canción",
                    "artist": item.get("channel") or item.get("uploader") or "",
                    "thumbnail": thumb,
                    "duration": item.get("duration"),
                    "source_url": f"https://www.youtube.com/watch?v={vid}",
                }
            )
        return result

    def prepare_audio(self, url: str) -> dict:
        video_id = self._video_id(url)
        existing = next(self.audio_dir.glob(f"{video_id}__*.mp3"), None)
        if existing and existing.stat().st_size > 1024:
            return self._metadata_for_existing(url, existing, video_id, "audio")

        self._assert_not_in_cooldown(video_id)

        last_error: Exception | None = None
        for profile_name, profile_opts in self._download_profiles():
            opts = self._base_opts() | profile_opts | {
                "format": "bestaudio/best",
                "outtmpl": str(self.audio_dir / f"{video_id}__%(title).120s.%(ext)s"),
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
                "windowsfilenames": False,
            }

            try:
                logger.info("audio extraction attempt video=%s profile=%s", video_id, profile_name)
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)

                mp3 = next(self.audio_dir.glob(f"{video_id}__*.mp3"), None)
                if not mp3 or mp3.stat().st_size <= 1024:
                    raise RuntimeError("No se generó el MP3")

                self._blocked_until.pop(video_id, None)
                logger.info("audio extraction succeeded video=%s profile=%s", video_id, profile_name)
                return self._result(info, mp3, video_id)
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "audio extraction profile failed video=%s profile=%s reason=%s",
                    video_id,
                    profile_name,
                    str(exc)[:240],
                )

        if last_error and self._looks_like_youtube_block(last_error):
            self._mark_blocked(video_id)
            if not self._has_cookies():
                raise MediaTemporarilyUnavailable(
                    "YouTube bloqueó la descarga desde el servidor. "
                    "El backend probó varios clientes automáticamente; "
                    "si persiste, configura las cookies de YouTube en Railway."
                ) from last_error
            raise MediaTemporarilyUnavailable(
                "YouTube rechazó temporalmente la sesión configurada. "
                "Las cookies pueden haber expirado."
            ) from last_error

        raise RuntimeError(f"No se pudo preparar el audio: {last_error}")

    def prepare_video(self, url: str) -> dict:
        video_id = self._video_id(url)
        existing = next(self.video_dir.glob(f"{video_id}__*.mp4"), None)
        if existing and existing.stat().st_size > 1024:
            return self._metadata_for_existing(url, existing, video_id, "video")

        self._assert_not_in_cooldown(video_id)

        last_error: Exception | None = None
        for profile_name, profile_opts in self._download_profiles():
            opts = self._base_opts() | profile_opts | {
                "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best",
                "merge_output_format": "mp4",
                "outtmpl": str(self.video_dir / f"{video_id}__%(title).120s.%(ext)s"),
                "windowsfilenames": False,
            }

            try:
                logger.info("video extraction attempt video=%s profile=%s", video_id, profile_name)
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)

                mp4 = next(self.video_dir.glob(f"{video_id}__*.mp4"), None)
                if not mp4 or mp4.stat().st_size <= 1024:
                    raise RuntimeError("No se generó el MP4")

                self._blocked_until.pop(video_id, None)
                logger.info("video extraction succeeded video=%s profile=%s", video_id, profile_name)
                return self._result(info, mp4, video_id)
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "video extraction profile failed video=%s profile=%s reason=%s",
                    video_id,
                    profile_name,
                    str(exc)[:240],
                )

        if last_error and self._looks_like_youtube_block(last_error):
            self._mark_blocked(video_id)
            if not self._has_cookies():
                raise MediaTemporarilyUnavailable(
                    "YouTube bloqueó la descarga desde el servidor. "
                    "El backend probó varios clientes automáticamente; "
                    "si persiste, configura las cookies de YouTube en Railway."
                ) from last_error
            raise MediaTemporarilyUnavailable(
                "YouTube rechazó temporalmente la sesión configurada. "
                "Las cookies pueden haber expirado."
            ) from last_error

        raise RuntimeError(f"No se pudo preparar el video: {last_error}")

    def _metadata_for_existing(
        self, url: str, path: Path, video_id: str, kind: str
    ) -> dict:
        opts = self._base_opts() | {"skip_download": True}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception:
            info = {
                "id": video_id,
                "title": path.stem.split("__", 1)[-1],
                "webpage_url": url,
            }
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

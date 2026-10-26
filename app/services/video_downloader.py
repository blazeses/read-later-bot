import asyncio
import os
import re
import shutil
import tempfile
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

try:
    from app.core.logger import log
except Exception:
    import logging
    log = logging.getLogger("video_downloader")

# Regex for video domains supported by yoinks / yt-dlp
VIDEO_DOMAINS_REGEX = re.compile(
    r"(?:youtube\.com|youtu\.be|tiktok\.com|instagram\.com|twitter\.com|x\.com|"
    r"reddit\.com|v\.redd\.it|vimeo\.com|threads\.net|twitch\.tv|facebook\.com|"
    r"fb\.watch|pinterest\.com|pin\.it|vk\.com|vkvideo\.ru|rutube\.ru|dailymotion\.com)",
    re.IGNORECASE,
)

VIDEO_EXT_REGEX = re.compile(r"\.(mp4|mkv|webm|mov|avi|flv|m4v)(\?.*)?$", re.IGNORECASE)


def is_video_url(url: Optional[str]) -> bool:
    """Check if the given URL is a known video platform or video file."""
    if not url or not url.startswith("http"):
        return False
    if VIDEO_DOMAINS_REGEX.search(url):
        return True
    if VIDEO_EXT_REGEX.search(url):
        return True
    return False


def format_duration(seconds: Optional[int]) -> str:
    """Format duration in seconds to MM:SS or HH:MM:SS."""
    if not seconds or seconds <= 0:
        return "0:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _find_ffmpeg() -> Optional[str]:
    """Find ffmpeg binary in PATH or Windows User Environment registry."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            val, _ = winreg.QueryValueEx(key, "Path")
            for p in val.split(";"):
                candidate = os.path.join(p.strip(), "ffmpeg.exe")
                if os.path.exists(candidate):
                    return candidate
    except Exception:
        pass
    return None


def _get_ytdlp_opts(extra_opts: Optional[dict] = None) -> dict:
    """Default secure and quiet options for yt-dlp."""
    ffmpeg_path = _find_ffmpeg()
    opts = {
        "quiet": True,
        "no_warnings": True,
        "no_color": True,
        "noplaylist": True,
        "socket_timeout": 20,
    }
    if ffmpeg_path:
        opts["ffmpeg_location"] = os.path.dirname(ffmpeg_path)
    if extra_opts:
        opts.update(extra_opts)
    return opts


def _extract_info_sync(url: str) -> Optional[Dict[str, Any]]:
    """Synchronous metadata extraction via yt-dlp."""
    try:
        import yt_dlp
    except ImportError:
        log.warning("yt-dlp не установлен")
        return None

    opts = _get_ytdlp_opts({"skip_download": True})
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None
            return {
                "title": info.get("title") or "Видео",
                "uploader": info.get("uploader") or info.get("channel") or info.get("creator"),
                "duration": info.get("duration"),
                "duration_str": format_duration(info.get("duration")),
                "thumbnail": info.get("thumbnail"),
                "description": (info.get("description") or "")[:500],
                "extractor": info.get("extractor_key") or info.get("extractor"),
                "webpage_url": info.get("webpage_url") or url,
                "filesize_approx": info.get("filesize_approx") or info.get("filesize"),
                "width": info.get("width"),
                "height": info.get("height"),
            }
    except Exception as e:
        log.warning(f"Ошибка извлечения метаданных видео через yt-dlp для {url}: {e}")
        return None


async def extract_video_metadata(url: str) -> Optional[Dict[str, Any]]:
    """Asynchronously extract video metadata."""
    return await asyncio.to_thread(_extract_info_sync, url)


def _download_video_sync(
    url: str,
    target_dir: str,
    max_mb: int = 50,
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    """
    Synchronous video download with yt-dlp constrained to max_mb (default 50MB for Telegram).
    """
    try:
        import yt_dlp
    except ImportError:
        return None, None, "Библиотека yt-dlp не установлена."

    max_bytes = max_mb * 1024 * 1024
    outtmpl = os.path.join(target_dir, "%(title).80s_%(id)s.%(ext)s")

    # Format strategy:
    # 1. Progressive mp4 under max_bytes or combined video+audio under max_bytes
    # 2. Prefer 720p or 480p to fit under 50MB
    format_selector = (
        f"best[filesize<{max_bytes}][ext=mp4]/"
        f"bestvideo[filesize<{int(max_bytes * 0.8)}]+bestaudio[filesize<{int(max_bytes * 0.2)}]/"
        f"best[filesize<{max_bytes}]/"
        f"best[height<=720][filesize<{max_bytes}]/"
        f"best[height<=480]/"
        f"best"
    )

    opts = _get_ytdlp_opts({
        "outtmpl": outtmpl,
        "format": format_selector,
        "max_filesize": max_bytes,
        "merge_output_format": "mp4",
    })

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return None, None, "Не удалось получить данные о видео."

            # Find downloaded file
            target_file = None
            if "requested_downloads" in info and info["requested_downloads"]:
                target_file = info["requested_downloads"][0].get("filepath")

            if not target_file or not os.path.exists(target_file):
                # Search in target_dir for any video file
                candidates = [
                    os.path.join(target_dir, f)
                    for f in os.listdir(target_dir)
                    if not f.endswith(".part") and not f.endswith(".ytdl")
                ]
                if candidates:
                    target_file = max(candidates, key=os.path.getmtime)

            if not target_file or not os.path.exists(target_file):
                return None, None, "Файл видео не найден после загрузки."

            size = os.path.getsize(target_file)
            if size > max_bytes:
                os.remove(target_file)
                return (
                    None,
                    None,
                    f"Размер видео ({size // (1024 * 1024)} МБ) превышает лимит Telegram Bot API (50 МБ).",
                )

            meta = {
                "title": info.get("title") or "Видео",
                "uploader": info.get("uploader") or info.get("channel") or info.get("creator"),
                "duration": info.get("duration"),
                "width": info.get("width"),
                "height": info.get("height"),
                "filesize": size,
            }
            return target_file, meta, None

    except Exception as e:
        err_msg = str(e)
        if "File is larger than max-filesize" in err_msg or "max_filesize" in err_msg:
            return None, None, "Размер видео превышает лимит Telegram (50 МБ)."
        log.exception(f"Ошибка загрузки видео {url}: {e}")
        return None, None, f"Ошибка загрузки: {err_msg[:200]}"


async def download_video(
    url: str,
    target_dir: Optional[str] = None,
    max_mb: int = 50,
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    """Asynchronously download video."""
    cleanup_dir = False
    if not target_dir:
        target_dir = tempfile.mkdtemp(prefix="yoinks_vid_")
    return await asyncio.to_thread(_download_video_sync, url, target_dir, max_mb)


def _download_audio_sync(
    url: str,
    target_dir: str,
    max_mb: int = 50,
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    """
    Synchronous audio extraction with yt-dlp (MP3/M4A).
    """
    try:
        import yt_dlp
    except ImportError:
        return None, None, "Библиотека yt-dlp не установлена."

    max_bytes = max_mb * 1024 * 1024
    outtmpl = os.path.join(target_dir, "%(title).80s_%(id)s.%(ext)s")

    opts = _get_ytdlp_opts({
        "outtmpl": outtmpl,
        "format": "bestaudio/best",
        "max_filesize": max_bytes,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ] if (_find_ffmpeg() or shutil.which("ffmpeg")) else [],
    })

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return None, None, "Не удалось получить аудиодорожку."

            target_file = None
            if "requested_downloads" in info and info["requested_downloads"]:
                target_file = info["requested_downloads"][0].get("filepath")

            if not target_file or not os.path.exists(target_file):
                candidates = [
                    os.path.join(target_dir, f)
                    for f in os.listdir(target_dir)
                    if not f.endswith(".part") and not f.endswith(".ytdl")
                ]
                if candidates:
                    target_file = max(candidates, key=os.path.getmtime)

            if not target_file or not os.path.exists(target_file):
                return None, None, "Аудиофайл не найден после загрузки."

            size = os.path.getsize(target_file)
            if size > max_bytes:
                os.remove(target_file)
                return (
                    None,
                    None,
                    f"Размер аудио ({size // (1024 * 1024)} МБ) превышает лимит Telegram (50 МБ).",
                )

            meta = {
                "title": info.get("title") or "Аудиозапись",
                "uploader": info.get("uploader") or info.get("channel") or info.get("creator"),
                "duration": info.get("duration"),
                "filesize": size,
            }
            return target_file, meta, None

    except Exception as e:
        err_msg = str(e)
        log.exception(f"Ошибка загрузки аудио {url}: {e}")
        return None, None, f"Ошибка загрузки аудио: {err_msg[:200]}"


async def download_audio(
    url: str,
    target_dir: Optional[str] = None,
    max_mb: int = 50,
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    """Asynchronously download audio."""
    if not target_dir:
        target_dir = tempfile.mkdtemp(prefix="yoinks_aud_")
    return await asyncio.to_thread(_download_audio_sync, url, target_dir, max_mb)

import asyncio
import os
import re
import shutil
import tempfile
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

try:
    from app.core.config import settings
    from app.core.logger import log
except Exception:
    settings = None
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


def _resolve_cookiefile() -> Optional[str]:
    """Resolve cookie file path from settings, environment variables, or local project files."""
    try:
        # 1. Configured path in settings or env COOKIES_FILE
        custom_path = getattr(settings, "COOKIES_FILE", None) or os.getenv("COOKIES_FILE")
        if custom_path and os.path.exists(custom_path):
            return custom_path

        # 2. Local files in project root or current working directory
        cwd = os.getcwd()
        for candidate in ("cookies.txt", "youtube_cookies.txt", "/etc/secrets/cookies.txt", os.path.join(cwd, "cookies.txt")):
            if os.path.exists(candidate) and os.path.isfile(candidate) and os.path.getsize(candidate) > 10:
                return os.path.abspath(candidate)

        # 3. Environment variable YOUTUBE_COOKIES or COOKIES_CONTENT
        env_cookies = getattr(settings, "YOUTUBE_COOKIES", None) or os.getenv("YOUTUBE_COOKIES") or os.getenv("COOKIES_CONTENT")
        if env_cookies and len(env_cookies.strip()) > 20:
            tmp_cookie = os.path.join(tempfile.gettempdir(), "ytdlp_runtime_cookies.txt")
            with open(tmp_cookie, "w", encoding="utf-8") as f:
                f.write(env_cookies.strip())
            return tmp_cookie
    except Exception as e:
        log.warning(f"Error resolving cookiefile: {e}")
    return None


def _get_ytdlp_opts(extra_opts: Optional[dict] = None) -> dict:
    """Default secure and quiet options for yt-dlp with cookie support and anti-bot bypass."""
    ffmpeg_path = _find_ffmpeg()
    opts = {
        "quiet": True,
        "no_warnings": True,
        "no_color": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "geo_bypass": True,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }

    cookie_path = _resolve_cookiefile()
    if cookie_path:
        opts["cookiefile"] = cookie_path
    else:
        # Modern client fallbacks when cookies are not provided
        opts["extractor_args"] = {
            "youtube": {
                "player_client": ["tv_samsung", "visionos", "web", "mweb"],
            }
        }

    if ffmpeg_path:
        opts["ffmpeg_location"] = os.path.dirname(ffmpeg_path)
    if extra_opts:
        opts.update(extra_opts)
    return opts


def _ensure_ytdlp():
    """Ensure yt-dlp is available, installing it on the fly if needed."""
    try:
        import yt_dlp
        return yt_dlp
    except ImportError:
        import subprocess
        import sys
        log.info("yt-dlp не найден в окружении. Запуск автоматической установки через pip...")
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "yt-dlp"],
                check=True,
                capture_output=True,
                timeout=90,
            )
            import yt_dlp
            log.info("yt-dlp успешно установлен автоматически!")
            return yt_dlp
        except Exception as e:
            log.warning(f"Автоматическая установка yt-dlp не удалась: {e}")
            return None


def _extract_info_sync(url: str) -> Optional[Dict[str, Any]]:
    """Synchronous metadata extraction via yt-dlp."""
    yt_dlp = _ensure_ytdlp()
    if not yt_dlp:
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


def split_video_into_parts(
    file_path: str,
    max_part_size_mb: int = 48,
) -> list[str]:
    """
    If file exceeds max_part_size_mb, split it into smaller playable mp4 parts using ffmpeg.
    Returns list of file paths (either [file_path] if no split needed, or [part1, part2, ...]).
    """
    if not os.path.exists(file_path):
        return []

    size = os.path.getsize(file_path)
    max_bytes = max_part_size_mb * 1024 * 1024
    if size <= max_bytes:
        return [file_path]

    ffmpeg_bin = _find_ffmpeg() or shutil.which("ffmpeg")
    if not ffmpeg_bin:
        log.warning("ffmpeg не найден для нарезки видео, возвращается исходный файл")
        return [file_path]

    import subprocess
    duration = None
    try:
        ffprobe_bin = os.path.join(os.path.dirname(ffmpeg_bin), "ffprobe.exe") if os.name == "nt" else "ffprobe"
        if not os.path.exists(ffprobe_bin):
            ffprobe_bin = shutil.which("ffprobe") or "ffprobe"
        cmd = [
            ffprobe_bin,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path,
        ]
        out = subprocess.check_output(cmd, timeout=15).decode().strip()
        duration = float(out)
    except Exception as e:
        log.warning(f"Не удалось определить длительность через ffprobe: {e}")

    total_mb = size / (1024 * 1024)
    if duration and duration > 10:
        segment_seconds = max(30, int(duration * (max_part_size_mb / total_mb)))
    else:
        segment_seconds = 300

    dirname = os.path.dirname(file_path)
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    out_pattern = os.path.join(dirname, f"{base_name}_part_%03d.mp4")

    split_cmd = [
        ffmpeg_bin,
        "-y",
        "-i", file_path,
        "-c", "copy",
        "-map", "0",
        "-segment_time", str(segment_seconds),
        "-f", "segment",
        "-reset_timestamps", "1",
        out_pattern,
    ]

    try:
        subprocess.run(split_cmd, check=True, capture_output=True, timeout=180)
        parts = sorted([
            os.path.join(dirname, f)
            for f in os.listdir(dirname)
            if f.startswith(f"{base_name}_part_") and f.endswith(".mp4")
        ])
        if parts:
            try:
                os.remove(file_path)
            except Exception:
                pass
            return parts
    except Exception as e:
        log.exception(f"Ошибка при нарезке видео через ffmpeg: {e}")

    return [file_path]


def _download_video_sync(
    url: str,
    target_dir: str,
    quality: str = "auto",
) -> Tuple[Optional[list[str]], Optional[Dict[str, Any]], Optional[str]]:
    """
    Synchronous video download with requested quality (1080, 720, 480, 360, auto).
    If the video exceeds Telegram limit (50MB), it is automatically split into playable parts.
    """
    yt_dlp = _ensure_ytdlp()
    if not yt_dlp:
        return None, None, "Библиотека yt-dlp не установлена."

    outtmpl = os.path.join(target_dir, "%(title).80s_%(id)s.%(ext)s")

    # Format strategy based on user requested quality:
    if quality in ("2160", "4k"):
        format_selector = "bestvideo[height<=2160]+bestaudio/best[height<=2160]/best"
    elif quality in ("1440", "2k"):
        format_selector = "bestvideo[height<=1440]+bestaudio/best[height<=1440]/best"
    elif quality == "1080":
        format_selector = "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
    elif quality == "720":
        format_selector = "bestvideo[height<=720]+bestaudio/best[height<=720]/best"
    elif quality == "480":
        format_selector = "bestvideo[height<=480]+bestaudio/best[height<=480]/best"
    elif quality == "360":
        format_selector = "bestvideo[height<=360]+bestaudio/best[height<=360]/best"
    else:  # auto (prefer single file under 50MB)
        format_selector = (
            "best[filesize<49M][ext=mp4]/"
            "bestvideo[filesize<38M]+bestaudio[filesize<10M]/"
            "best[filesize<49M]/"
            "best[height<=720][filesize<49M]/"
            "best[height<=480]/"
            "best"
        )

    opts = _get_ytdlp_opts({
        "outtmpl": outtmpl,
        "format": format_selector,
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
            parts = [target_file]

            # If video is larger than 49MB (e.g. 1080p or long 1-2 hour video), split into parts for Telegram
            if size > (49 * 1024 * 1024):
                parts = split_video_into_parts(target_file, max_part_size_mb=48)
                if not parts:
                    parts = [target_file]

            direct_url = info.get("url")
            if not direct_url and info.get("requested_downloads"):
                direct_url = info["requested_downloads"][0].get("url")

            meta = {
                "title": info.get("title") or "Видео",
                "uploader": info.get("uploader") or info.get("channel") or info.get("creator"),
                "duration": info.get("duration"),
                "width": info.get("width"),
                "height": info.get("height"),
                "filesize": size,
                "parts_count": len(parts),
                "direct_url": direct_url,
                "quality": quality,
            }
            return parts, meta, None

    except Exception as e:
        err_msg = str(e)
        log.exception(f"Ошибка загрузки видео {url}: {e}")
        return None, None, f"Ошибка загрузки: {err_msg[:200]}"


async def download_video(
    url: str,
    target_dir: Optional[str] = None,
    quality: str = "auto",
) -> Tuple[Optional[list[str]], Optional[Dict[str, Any]], Optional[str]]:
    """Asynchronously download video with specified quality and automatic part-splitting for large videos."""
    if not target_dir:
        target_dir = tempfile.mkdtemp(prefix="yoinks_vid_")
    return await asyncio.to_thread(_download_video_sync, url, target_dir, quality)


def _download_audio_sync(
    url: str,
    target_dir: str,
    max_mb: int = 50,
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    """
    Synchronous audio extraction with yt-dlp (MP3/M4A).
    """
    yt_dlp = _ensure_ytdlp()
    if not yt_dlp:
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

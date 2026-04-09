from __future__ import annotations

import asyncio
import html
import json
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp
import tkinter as tk
import ttkbootstrap as tb
from bilibili_api.utils.network import Credential, HEADERS as BILIBILI_HEADERS
from bilibili_api.utils.parse_link import ResourceType, parse_link
from bilibili_api.video import (
    AudioStreamDownloadURL,
    FLVStreamDownloadURL,
    MP4StreamDownloadURL,
    VideoDownloadURLDataDetecter,
    VideoStreamDownloadURL,
)
from tkinter import filedialog, messagebox, ttk

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
CONFIG_PATH = APP_DIR / "app_config.json"
CREDENTIAL_PATH = APP_DIR / "credential.json"
FFMPEG_BIN = "ffmpeg"
CHUNK_SIZE = 256 * 1024
UPDATE_INTERVAL_SECONDS = 0.5
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
SUPPORTED_URL_PATTERN = re.compile(
    r"https?://[^\s<>'\"，。；：！？、（）【】《》]+",
    re.IGNORECASE,
)
TRAILING_URL_PUNCTUATION = ".,;:!?)]}>\"'，。；：！？、）】》"
ERROR_LOG_PATH = APP_DIR / "video_manager_error.log"


@dataclass(slots=True)
class DownloadTask:
    task_id: str
    url: str
    custom_name: str
    output_dir: str
    skip_existing: bool = False
    title: str = "等待解析"
    status: str = "排队中"
    progress_text: str = "0%"
    speed_text: str = "-"
    message: str = ""
    output_file: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class TaskEvent:
    task_id: str
    title: str | None = None
    status: str | None = None
    progress_text: str | None = None
    speed_text: str | None = None
    message: str | None = None
    output_file: str | None = None


def format_bytes(num_bytes: float) -> str:
    value = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def normalize_custom_name(raw_name: str) -> str:
    name = raw_name.strip().strip('"').strip("'")
    suffix = Path(name).suffix.lower()
    if suffix in {".mp4", ".flv", ".m4a", ".m4s", ".mkv"}:
        name = name[: -len(suffix)]
    return name.strip()


def sanitize_filename(raw_name: str) -> str:
    cleaned = INVALID_FILENAME_CHARS.sub("_", raw_name)
    cleaned = cleaned.replace("\n", " ").replace("\r", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "bilibili-video"


def is_supported_bilibili_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().split(":", 1)[0]
    return host == "b23.tv" or host.endswith(".bilibili.com") or host == "bilibili.com"


def extract_supported_urls(raw_text: str) -> list[str]:
    text = html.unescape(raw_text or "")
    urls: list[str] = []
    seen: set[str] = set()
    for match in SUPPORTED_URL_PATTERN.findall(text):
        candidate = match.strip().rstrip(TRAILING_URL_PUNCTUATION)
        if not candidate or not is_supported_bilibili_url(candidate):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        urls.append(candidate)
    return urls


def ensure_unique_path(output_dir: Path, base_name: str, suffix: str) -> Path:
    candidate = output_dir / f"{base_name}{suffix}"
    index = 1
    while candidate.exists():
        candidate = output_dir / f"{base_name} ({index}){suffix}"
        index += 1
    return candidate


def load_json_file(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    return data if isinstance(data, dict) else default


def load_config() -> dict:
    default_dir = str(DATA_DIR if DATA_DIR.exists() else APP_DIR)
    config = load_json_file(
        CONFIG_PATH,
        {
            "output_dir": default_dir,
            "max_workers": 1,
            "expand_single_video_to_all_pages": False,
            "skip_existing_files": True,
        },
    )
    config["output_dir"] = str(Path(config.get("output_dir", default_dir)))
    try:
        max_workers = int(config.get("max_workers", 1))
    except (TypeError, ValueError):
        max_workers = 1
    config["max_workers"] = min(max(max_workers, 1), 8)
    config["expand_single_video_to_all_pages"] = bool(
        config.get("expand_single_video_to_all_pages", False)
    )
    config["skip_existing_files"] = bool(config.get("skip_existing_files", True))
    return config


def save_config(
    output_dir: str,
    max_workers: int,
    expand_single_video_to_all_pages: bool,
    skip_existing_files: bool,
) -> None:
    payload = {
        "output_dir": output_dir,
        "max_workers": min(max(max_workers, 1), 8),
        "expand_single_video_to_all_pages": bool(expand_single_video_to_all_pages),
        "skip_existing_files": bool(skip_existing_files),
    }
    CONFIG_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_credential() -> Credential:
    if not CREDENTIAL_PATH.exists():
        return Credential()

    try:
        raw_data = json.loads(CREDENTIAL_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"credential.json 不是合法的 JSON: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"读取 credential.json 失败: {exc}") from exc

    if not isinstance(raw_data, dict):
        raise ValueError("credential.json 必须是 JSON 对象。")

    def read_value(*keys: str) -> str | None:
        for key in keys:
            value = raw_data.get(key)
            if value:
                return str(value).strip()
        return None

    return Credential(
        sessdata=read_value("sessdata", "SESSDATA"),
        bili_jct=read_value("bili_jct", "BILI_JCT"),
        buvid3=read_value("buvid3", "BUVID3"),
        buvid4=read_value("buvid4", "BUVID4"),
        dedeuserid=read_value("dedeuserid", "DedeUserID"),
        ac_time_value=read_value("ac_time_value", "AC_TIME_VALUE"),
    )


def extract_page_index(url: str, info: dict) -> int:
    parsed = urlparse(url.strip())
    query = parse_qs(parsed.query)
    raw_page = query.get("p", ["1"])[0]
    try:
        page_index = max(int(raw_page), 1) - 1
    except ValueError:
        page_index = 0

    pages = info.get("pages") or []
    if isinstance(pages, list) and pages:
        return min(page_index, len(pages) - 1)
    return 0


def build_page_url(url: str, page_number: int) -> str:
    parsed = urlparse(url.strip())
    query_items = parse_qs(parsed.query, keep_blank_values=True)
    query_items["p"] = [str(max(page_number, 1))]
    flattened_query_items: list[tuple[str, str]] = []
    for key, values in query_items.items():
        if not values:
            flattened_query_items.append((key, ""))
            continue
        for value in values:
            flattened_query_items.append((key, value))
    return parsed._replace(query=urlencode(flattened_query_items, doseq=True)).geturl()


def build_default_title(info: dict, page_index: int) -> str:
    title = str(info.get("title") or "bilibili-video").strip()
    pages = info.get("pages") or []
    if not isinstance(pages, list) or not pages:
        return title

    if len(pages) <= 1:
        return title

    current_page = pages[page_index]
    page_part = str(current_page.get("part") or "").strip()
    if page_part and page_part != title:
        return page_part
    return f"P{page_index + 1}"


def build_progress_text(downloaded_bytes: int, total_bytes: int) -> str:
    if total_bytes > 0:
        ratio = min(downloaded_bytes / total_bytes, 0.999)
        return f"{ratio * 100:5.1f}% | {format_bytes(downloaded_bytes)}/{format_bytes(total_bytes)}"
    return format_bytes(downloaded_bytes)


def run_ffmpeg(arguments: list[str]) -> None:
    command = [FFMPEG_BIN, "-y", *arguments]
    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if process.returncode != 0:
        details = process.stderr.strip() or process.stdout.strip() or "ffmpeg 执行失败。"
        raise RuntimeError(details)


def write_error_log(details: str) -> Path | None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        ERROR_LOG_PATH.write_text(
            f"[{timestamp}]\n{details.strip()}\n",
            encoding="utf-8",
        )
    except OSError:
        return None
    return ERROR_LOG_PATH


async def resolve_single_video_entries(url: str) -> list[tuple[str, str]]:
    credential = load_credential()
    resource, resource_type = await parse_link(url, credential=credential)
    if resource_type != ResourceType.VIDEO:
        raise ValueError("当前版本只支持普通 B 站视频链接。")

    info = await resource.get_info()
    pages = info.get("pages") or []
    if not isinstance(pages, list) or not pages:
        return [(url, str(info.get("title") or "待解析视频"))]

    if len(pages) <= 1:
        page_index = extract_page_index(url, info)
        return [(build_page_url(url, page_index + 1), build_default_title(info, page_index))]

    return [
        (build_page_url(url, page_index + 1), build_default_title(info, page_index))
        for page_index in range(len(pages))
    ]


class DownloadManager:
    def __init__(self, event_queue: queue.Queue[TaskEvent], max_workers: int = 3) -> None:
        self.event_queue = event_queue
        self.max_workers = max(1, max_workers)
        self._lock = threading.Lock()
        self._pending: deque[DownloadTask] = deque()
        self._active_task_ids: set[str] = set()

    def set_max_workers(self, value: int) -> None:
        with self._lock:
            self.max_workers = min(max(int(value), 1), 8)
            self._schedule_locked()

    def submit(self, task: DownloadTask) -> None:
        with self._lock:
            self._pending.append(task)
            self._schedule_locked()

    def remove_pending(self, task_id: str) -> bool:
        with self._lock:
            for queued_task in list(self._pending):
                if queued_task.task_id == task_id:
                    self._pending.remove(queued_task)
                    self._emit(task_id, status="已移除", message="任务已从队列中移除。")
                    return True
        return False

    def is_active(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._active_task_ids

    def get_counts(self) -> tuple[int, int]:
        with self._lock:
            return len(self._active_task_ids), len(self._pending)

    def _schedule_locked(self) -> None:
        while self._pending and len(self._active_task_ids) < self.max_workers:
            task = self._pending.popleft()
            self._active_task_ids.add(task.task_id)
            worker = threading.Thread(
                target=self._run_task,
                args=(task,),
                daemon=True,
                name=f"download-{task.task_id}",
            )
            worker.start()

    def _run_task(self, task: DownloadTask) -> None:
        try:
            asyncio.run(self._download_task(task))
        except Exception as exc:
            error_text = str(exc).strip() or exc.__class__.__name__
            self._emit(
                task.task_id,
                status="失败",
                speed_text="-",
                message=f"任务失败：{error_text}",
            )
        finally:
            with self._lock:
                self._active_task_ids.discard(task.task_id)
                self._schedule_locked()

    async def _download_task(self, task: DownloadTask) -> None:
        self._emit(task.task_id, status="解析链接", progress_text="0%", speed_text="-")
        credential = load_credential()
        resource, resource_type = await parse_link(task.url, credential=credential)

        if resource_type != ResourceType.VIDEO:
            raise ValueError("当前版本只支持普通 B 站视频链接。")

        self._emit(task.task_id, status="获取视频信息")
        info = await resource.get_info()
        page_index = extract_page_index(task.url, info)

        resolved_name = normalize_custom_name(task.custom_name) or build_default_title(
            info, page_index
        )
        safe_name = sanitize_filename(resolved_name)
        output_dir = Path(task.output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        preferred_path = output_dir / f"{safe_name}.mp4"
        if task.skip_existing and preferred_path.exists():
            self._emit(
                task.task_id,
                title=resolved_name,
                status="已跳过",
                progress_text="100%",
                speed_text="-",
                output_file=str(preferred_path),
                message=f"检测到同名文件，已跳过：{preferred_path.name}",
            )
            return
        final_path = ensure_unique_path(output_dir, safe_name, ".mp4")

        self._emit(
            task.task_id,
            title=resolved_name,
            status="获取下载地址",
            output_file=str(final_path),
            message=f"开始处理：{resolved_name}",
        )

        download_data = await resource.get_download_url(page_index=page_index)
        detecter = VideoDownloadURLDataDetecter(download_data)
        selected_streams = [stream for stream in detecter.detect_best_streams() if stream]
        if not selected_streams:
            raise RuntimeError("没有找到可用的视频流。")

        temp_dir = Path(tempfile.mkdtemp(prefix="bili-task-"))
        try:
            timeout = aiohttp.ClientTimeout(total=None, connect=20, sock_read=120)
            headers = dict(BILIBILI_HEADERS)
            cookies = credential.get_cookies()

            async with aiohttp.ClientSession(timeout=timeout) as session:
                await self._download_stream_bundle(
                    session=session,
                    task=task,
                    streams=selected_streams,
                    headers=headers,
                    cookies=cookies,
                    temp_dir=temp_dir,
                    final_path=final_path,
                )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        self._emit(
            task.task_id,
            status="完成",
            progress_text="100%",
            speed_text="-",
            output_file=str(final_path),
            message=f"下载完成：{final_path}",
        )

    async def _download_stream_bundle(
        self,
        session: aiohttp.ClientSession,
        task: DownloadTask,
        streams: list,
        headers: dict[str, str],
        cookies: dict[str, str],
        temp_dir: Path,
        final_path: Path,
    ) -> None:
        video_stream = next(
            (stream for stream in streams if isinstance(stream, VideoStreamDownloadURL)),
            None,
        )
        audio_stream = next(
            (stream for stream in streams if isinstance(stream, AudioStreamDownloadURL)),
            None,
        )
        merged_mp4_stream = next(
            (
                stream
                for stream in streams
                if isinstance(stream, (MP4StreamDownloadURL, FLVStreamDownloadURL))
            ),
            None,
        )

        overall_start = time.monotonic()

        if merged_mp4_stream is not None:
            extension = ".mp4" if isinstance(merged_mp4_stream, MP4StreamDownloadURL) else ".flv"
            temp_media_path = temp_dir / f"source{extension}"
            total_bytes = await self._probe_content_length(
                session, merged_mp4_stream.url, headers, cookies
            )
            await self._download_file(
                session=session,
                task_id=task.task_id,
                status="下载视频",
                url=merged_mp4_stream.url,
                destination=temp_media_path,
                headers=headers,
                cookies=cookies,
                overall_start=overall_start,
                completed_before=0,
                combined_total=total_bytes,
            )

            self._emit(task.task_id, status="整理文件", progress_text="99%", speed_text="-")
            if temp_media_path.suffix == ".mp4":
                shutil.move(temp_media_path, final_path)
            else:
                run_ffmpeg(
                    [
                        "-loglevel",
                        "error",
                        "-i",
                        str(temp_media_path),
                        "-c",
                        "copy",
                        str(final_path),
                    ]
                )
            return

        if video_stream is None:
            raise RuntimeError("当前视频没有可下载的视频轨道。")

        video_total = await self._probe_content_length(session, video_stream.url, headers, cookies)
        audio_total = 0
        if audio_stream is not None:
            audio_total = await self._probe_content_length(
                session, audio_stream.url, headers, cookies
            )
        combined_total = video_total + audio_total

        downloaded_video_bytes = await self._download_file(
            session=session,
            task_id=task.task_id,
            status="下载视频流",
            url=video_stream.url,
            destination=temp_dir / "video.m4s",
            headers=headers,
            cookies=cookies,
            overall_start=overall_start,
            completed_before=0,
            combined_total=combined_total,
        )

        downloaded_audio_bytes = 0
        audio_temp_path = temp_dir / "audio.m4s"
        if audio_stream is not None:
            downloaded_audio_bytes = await self._download_file(
                session=session,
                task_id=task.task_id,
                status="下载音频流",
                url=audio_stream.url,
                destination=audio_temp_path,
                headers=headers,
                cookies=cookies,
                overall_start=overall_start,
                completed_before=downloaded_video_bytes,
                combined_total=max(combined_total, downloaded_video_bytes + audio_total),
            )

        self._emit(
            task.task_id,
            status="合并文件",
            progress_text="99%",
            speed_text="-",
        )

        video_temp_path = temp_dir / "video.m4s"
        if audio_stream is None:
            run_ffmpeg(
                [
                    "-loglevel",
                    "error",
                    "-i",
                    str(video_temp_path),
                    "-c",
                    "copy",
                    str(final_path),
                ]
            )
            return

        if downloaded_audio_bytes <= 0:
            raise RuntimeError("音频流下载失败。")

        run_ffmpeg(
            [
                "-loglevel",
                "error",
                "-i",
                str(video_temp_path),
                "-i",
                str(audio_temp_path),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c",
                "copy",
                str(final_path),
            ]
        )

    async def _probe_content_length(
        self,
        session: aiohttp.ClientSession,
        url: str,
        headers: dict[str, str],
        cookies: dict[str, str],
    ) -> int:
        try:
            async with session.head(
                url,
                headers=headers,
                cookies=cookies,
                allow_redirects=True,
            ) as response:
                if response.status < 400:
                    header = response.headers.get("Content-Length")
                    if header and header.isdigit():
                        return int(header)
        except aiohttp.ClientError:
            pass

        range_headers = dict(headers)
        range_headers["Range"] = "bytes=0-0"
        try:
            async with session.get(
                url,
                headers=range_headers,
                cookies=cookies,
                allow_redirects=True,
            ) as response:
                if response.status >= 400:
                    return 0
                content_range = response.headers.get("Content-Range")
                if content_range and "/" in content_range:
                    size_text = content_range.rsplit("/", 1)[-1]
                    if size_text.isdigit():
                        return int(size_text)
                header = response.headers.get("Content-Length")
                if header and header.isdigit():
                    return int(header)
        except aiohttp.ClientError:
            return 0
        return 0

    async def _download_file(
        self,
        session: aiohttp.ClientSession,
        task_id: str,
        status: str,
        url: str,
        destination: Path,
        headers: dict[str, str],
        cookies: dict[str, str],
        overall_start: float,
        completed_before: int,
        combined_total: int,
    ) -> int:
        downloaded_for_file = 0
        last_report_at = 0.0

        async with session.get(
            url,
            headers=headers,
            cookies=cookies,
            allow_redirects=True,
        ) as response:
            response.raise_for_status()
            response_total = int(response.headers.get("Content-Length") or 0)
            effective_total = max(combined_total, completed_before + response_total)

            with destination.open("wb") as file:
                async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                    if not chunk:
                        continue

                    file.write(chunk)
                    downloaded_for_file += len(chunk)
                    now = time.monotonic()
                    if now - last_report_at < UPDATE_INTERVAL_SECONDS:
                        continue

                    total_downloaded = completed_before + downloaded_for_file
                    elapsed = max(now - overall_start, 0.001)
                    self._emit(
                        task_id,
                        status=status,
                        progress_text=build_progress_text(total_downloaded, effective_total),
                        speed_text=f"{format_bytes(total_downloaded / elapsed)}/s",
                    )
                    last_report_at = now

        total_downloaded = completed_before + downloaded_for_file
        elapsed = max(time.monotonic() - overall_start, 0.001)
        self._emit(
            task_id,
            status=status,
            progress_text=build_progress_text(total_downloaded, max(combined_total, total_downloaded)),
            speed_text=f"{format_bytes(total_downloaded / elapsed)}/s",
        )
        return downloaded_for_file

    def _emit(self, task_id: str, **kwargs: str) -> None:
        self.event_queue.put(TaskEvent(task_id=task_id, **kwargs))


class VideoManagerApp:
    def __init__(self, root: tb.Window) -> None:
        self.root = root
        self.root.title("B 站本地下载")
        self.root.geometry("1240x720")
        self.root.minsize(1120, 620)

        config = load_config()
        self.name_var = tk.StringVar()
        self.output_dir_var = tk.StringVar(value=config["output_dir"])
        self.expand_collection_var = tk.BooleanVar(
            value=config["expand_single_video_to_all_pages"]
        )
        self.skip_existing_var = tk.BooleanVar(value=config["skip_existing_files"])

        self.current_task_var = tk.StringVar(value="尚未选择任务")
        self.status_var = tk.StringVar(value="等待任务")
        self.progress_text_var = tk.StringVar(value="")
        self.message_var = tk.StringVar(
            value="支持一次粘贴多行文本，程序会自动提取其中的 B 站链接并排队下载。"
        )
        self.output_path_var = tk.StringVar(value="")
        self.progress_value_var = tk.DoubleVar(value=0.0)
        self.queue_summary_var = tk.StringVar(value="总任务 0 | 下载中 0 | 排队 0 | 已完成 0")

        self.event_queue: queue.Queue[TaskEvent] = queue.Queue()
        self.collection_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.manager = DownloadManager(event_queue=self.event_queue, max_workers=1)
        self.tasks: dict[str, DownloadTask] = {}
        self.selected_task_id: str | None = None
        self.follow_active_task = True
        self._resolving_collection = False

        self._build_layout()
        self._refresh_queue_summary()
        self._poll_events()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind_all("<Control-Return>", lambda _event: self._start_download())

        if shutil.which(FFMPEG_BIN) is None:
            messagebox.showwarning(
                "ffmpeg 未找到",
                "没有检测到 ffmpeg，部分视频可能无法合并为 mp4。"
                "\n请先安装 ffmpeg 并确保它已经加入 PATH。",
            )

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        main = tb.Frame(self.root, padding=(30, 26))
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=3)
        main.columnconfigure(1, weight=2)
        main.rowconfigure(0, weight=1)

        left_panel = tb.Frame(main)
        left_panel.grid(row=0, column=0, sticky="nsew")
        left_panel.columnconfigure(0, weight=1)
        left_panel.rowconfigure(2, weight=1)

        form = tb.Frame(left_panel, padding=(0, 0))
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)

        label_font = ("Microsoft YaHei UI", 11, "bold")

        tb.Label(form, text="视频链接", font=label_font).grid(
            row=0, column=0, sticky="nw", padx=(0, 16), pady=(0, 16)
        )
        self.url_text = tk.Text(
            form,
            height=5,
            wrap="word",
            font=("Microsoft YaHei UI", 10),
            relief="solid",
            borderwidth=1,
        )
        self.url_text.grid(row=0, column=1, columnspan=2, sticky="ew", pady=(0, 8))
        self.expand_collection_check = tb.Checkbutton(
            form,
            text="单链接时展开整个分P合集",
            variable=self.expand_collection_var,
            bootstyle="round-toggle",
            command=self._save_settings,
        )
        self.expand_collection_check.grid(row=1, column=1, sticky="w", pady=(0, 16))
        self.skip_existing_check = tb.Checkbutton(
            form,
            text="不重复下载",
            variable=self.skip_existing_var,
            bootstyle="round-toggle",
            command=self._save_settings,
        )
        self.skip_existing_check.grid(row=1, column=2, sticky="w", padx=(18, 0), pady=(0, 16))

        tb.Label(form, text="视频命名", font=label_font).grid(
            row=2, column=0, sticky="w", padx=(0, 16), pady=(0, 16)
        )
        self.name_entry = tb.Entry(form, textvariable=self.name_var)
        self.name_entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(0, 16))

        tb.Label(form, text="保存目录", font=label_font).grid(
            row=3, column=0, sticky="w", padx=(0, 16)
        )
        self.dir_entry = tb.Entry(form, textvariable=self.output_dir_var)
        self.dir_entry.grid(row=3, column=1, columnspan=2, sticky="ew")

        action_bar = tb.Frame(left_panel)
        action_bar.grid(row=1, column=0, sticky="w", pady=(22, 0))
        self.browse_button = tb.Button(
            action_bar,
            text="选择目录",
            command=self._choose_output_dir,
            bootstyle="secondary-outline",
            width=12,
        )
        self.browse_button.grid(row=0, column=0, sticky="w")

        self.start_button = tb.Button(
            action_bar,
            text="加入下载队列",
            command=self._start_download,
            bootstyle="primary",
            width=18,
        )
        self.start_button.grid(row=0, column=1, sticky="w", padx=(14, 0))

        details = tb.Labelframe(left_panel, text="任务详情", padding=(20, 18))
        details.grid(row=2, column=0, sticky="nsew", pady=(24, 0))
        details.columnconfigure(0, weight=1)
        details.columnconfigure(1, weight=1)

        tb.Label(
            details,
            textvariable=self.current_task_var,
            font=("Microsoft YaHei UI", 12, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        self.status_badge = tb.Label(
            details,
            textvariable=self.status_var,
            bootstyle="secondary",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.status_badge.grid(row=1, column=0, sticky="w", pady=(12, 0))

        tb.Label(
            details,
            textvariable=self.progress_text_var,
            font=("Microsoft YaHei UI", 10),
            bootstyle="secondary",
        ).grid(row=1, column=1, sticky="e", pady=(12, 0))

        self.progressbar = tb.Progressbar(
            details,
            variable=self.progress_value_var,
            maximum=100,
            mode="determinate",
            bootstyle="success-striped",
        )
        self.progressbar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))

        tb.Label(
            details,
            textvariable=self.message_var,
            font=("Microsoft YaHei UI", 10),
            wraplength=620,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(12, 0))

        tb.Label(
            details,
            textvariable=self.output_path_var,
            font=("Consolas", 9),
            wraplength=620,
            justify="left",
            bootstyle="secondary",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

        queue_frame = tb.Labelframe(main, text="任务队列", padding=(20, 18))
        queue_frame.grid(row=0, column=1, sticky="nsew", padx=(24, 0))
        queue_frame.columnconfigure(0, weight=1)
        queue_frame.rowconfigure(0, weight=1)

        self.task_tree = ttk.Treeview(
            queue_frame,
            show="tree",
            height=18,
        )
        self.task_tree.column("#0", width=690, anchor="w")
        self.task_tree.grid(row=0, column=0, sticky="nsew")
        self.task_tree.bind("<ButtonRelease-1>", self._on_task_clicked)

        scrollbar = tb.Scrollbar(queue_frame, orient="vertical", command=self.task_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(10, 0))
        self.task_tree.configure(yscrollcommand=scrollbar.set)
        tb.Label(
            queue_frame,
            textvariable=self.queue_summary_var,
            bootstyle="secondary",
            font=("Microsoft YaHei UI", 10),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(12, 0))

        self.url_text.focus_set()

    def _choose_output_dir(self) -> None:
        initial_dir = self.output_dir_var.get().strip() or str(APP_DIR)
        selected = filedialog.askdirectory(initialdir=initial_dir)
        if selected:
            self.output_dir_var.set(selected)
            self._save_settings()

    def _set_collection_resolving(self, resolving: bool) -> None:
        self._resolving_collection = resolving
        self.start_button.configure(
            state="disabled" if resolving else "normal",
            text="解析合集..." if resolving else "加入下载队列",
        )

    def _set_collection_parse_state(self) -> None:
        self.current_task_var.set("合集解析中")
        self.status_var.set("解析合集")
        self.progress_text_var.set("")
        self.progress_value_var.set(0.0)
        self.message_var.set("正在读取分P列表，准备展开整个合集...")
        self.output_path_var.set("")
        self._update_status_style("解析合集")

    def _format_task_list_text(self, task: DownloadTask) -> str:
        if task.status in {"完成", "失败", "已移除", "已跳过"}:
            return f"{task.status} · {task.title}"
        if task.status and task.status != "排队中":
            return f"{task.status} · {task.title}"
        return task.title

    def _build_detail_message(self, task: DownloadTask) -> str:
        if task.speed_text not in {"", "-"} and task.status in {"下载视频", "下载视频流", "下载音频流"}:
            return f"速度 {task.speed_text}"
        if task.message:
            return task.message
        if task.status == "排队中":
            return "等待前序任务完成后开始下载。"
        if task.status == "解析链接":
            return "正在解析链接..."
        if task.status == "获取视频信息":
            return "正在获取视频信息..."
        if task.status == "获取下载地址":
            return "正在获取下载地址..."
        if task.status == "解析合集":
            return "正在读取分P列表，准备展开整个合集..."
        return ""

    def _build_expected_output_path(self, output_path: Path, task_name: str) -> Path:
        safe_name = sanitize_filename(task_name)
        return output_path / f"{safe_name}.mp4"

    def _find_duplicate_task(self, output_path: Path, task_name: str) -> DownloadTask | None:
        expected_path = self._build_expected_output_path(output_path, task_name)
        resolved_output_dir = output_path.resolve()
        for existing_task in self.tasks.values():
            if existing_task.status == "已移除":
                continue
            existing_output_dir = Path(existing_task.output_dir).expanduser().resolve()
            if existing_output_dir != resolved_output_dir:
                continue
            existing_name = normalize_custom_name(existing_task.custom_name) or existing_task.title
            if self._build_expected_output_path(resolved_output_dir, existing_name) == expected_path:
                return existing_task
        return None

    def _add_skipped_task(self, title: str, output_path: Path, reason: str) -> str:
        expected_path = self._build_expected_output_path(output_path, title)
        task = DownloadTask(
            task_id=uuid.uuid4().hex[:10],
            url="",
            custom_name="",
            output_dir=str(output_path),
            skip_existing=True,
            title=title,
            status="已跳过",
            progress_text="100%",
            speed_text="-",
            message=reason,
            output_file=str(expected_path),
        )
        self.tasks[task.task_id] = task
        self.task_tree.insert("", "end", iid=task.task_id, text=self._format_task_list_text(task))
        return task.task_id

    def _submit_collection_resolution(self, url: str, output_path: Path, custom_name: str) -> None:
        try:
            resolved_entries = asyncio.run(resolve_single_video_entries(url))
        except Exception as exc:
            self.collection_queue.put(("error", str(exc)))
            return

        self.collection_queue.put(
            (
                "resolved",
                {
                    "resolved_entries": resolved_entries,
                    "output_path": output_path,
                    "custom_name": custom_name,
                },
            )
        )

    def _handle_collection_resolution_error(self, error_text: str) -> None:
        self._set_collection_resolving(False)
        self.current_task_var.set("合集解析失败")
        self.status_var.set("失败")
        self.progress_text_var.set("")
        self.progress_value_var.set(0.0)
        self.message_var.set(f"无法展开合集：{error_text}")
        self.output_path_var.set("")
        self._update_status_style("失败")

    def _enqueue_resolved_entries(
        self,
        resolved_entries: list[tuple[str, str]],
        output_path: Path,
        custom_name: str,
        from_collection_expansion: bool,
    ) -> None:
        context = {
            "entries": resolved_entries,
            "output_path": output_path,
            "custom_name": custom_name,
            "from_collection_expansion": from_collection_expansion,
            "first_new_task_id": None,
            "index": 0,
            "should_select_first_task": (
                self.selected_task_id is None
                or self.status_var.get() in {"解析合集", "等待任务", "等待提交"}
            ),
        }
        self._set_url_text("")
        if len(resolved_entries) > 1:
            self.name_var.set("")
        self._enqueue_entries_batch(context)

    def _enqueue_entries_batch(self, context: dict) -> None:
        entries: list[tuple[str, str]] = context["entries"]
        batch_size = 2 if len(entries) >= 80 else 4
        start_index = context["index"]
        end_index = min(start_index + batch_size, len(entries))
        output_path: Path = context["output_path"]
        custom_name: str = context["custom_name"]
        skip_existing = self.skip_existing_var.get()

        for index in range(start_index, end_index):
            url, title_hint = entries[index]
            use_custom_name = len(entries) == 1
            placeholder_title = (
                custom_name if use_custom_name else title_hint or f"队列任务 {len(self.tasks) + 1}"
            )
            if skip_existing and placeholder_title:
                expected_path = self._build_expected_output_path(output_path, placeholder_title)
                duplicate_task = self._find_duplicate_task(output_path, placeholder_title)
                if expected_path.exists():
                    skipped_task_id = self._add_skipped_task(
                        title=placeholder_title,
                        output_path=output_path,
                        reason=f"检测到同名文件，已跳过：{expected_path.name}",
                    )
                    if context["first_new_task_id"] is None:
                        context["first_new_task_id"] = skipped_task_id
                    continue
                if duplicate_task is not None:
                    skipped_task_id = self._add_skipped_task(
                        title=placeholder_title,
                        output_path=output_path,
                        reason=f"队列中已存在同名任务，已跳过：{placeholder_title}",
                    )
                    if context["first_new_task_id"] is None:
                        context["first_new_task_id"] = skipped_task_id
                    continue
            task = DownloadTask(
                task_id=uuid.uuid4().hex[:10],
                url=url,
                custom_name=custom_name if use_custom_name else "",
                output_dir=str(output_path),
                skip_existing=skip_existing,
                title=placeholder_title or f"待解析视频 {index + 1}",
            )
            self.tasks[task.task_id] = task
            self.task_tree.insert("", "end", iid=task.task_id, text=self._format_task_list_text(task))
            self.manager.submit(task)
            if context["first_new_task_id"] is None:
                context["first_new_task_id"] = task.task_id

        context["index"] = end_index
        self._refresh_queue_summary()

        first_new_task_id = context["first_new_task_id"]
        if context["should_select_first_task"] and first_new_task_id is not None:
            self._show_task(first_new_task_id)

        if end_index < len(entries):
            if context["from_collection_expansion"]:
                self.current_task_var.set("合集解析完成")
                self.status_var.set("加入队列")
                self.message_var.set(f"正在加入任务 {end_index}/{len(entries)}...")
                self._update_status_style("加入队列")
            delay_ms = 24 if len(entries) >= 80 else 16
            self.root.after(delay_ms, lambda: self._enqueue_entries_batch(context))
            return

        if context["from_collection_expansion"] and len(entries) > 1:
            self.message_var.set(f"已把 1 个链接展开为 {len(entries)} 个分P任务。")
        self._set_collection_resolving(False)
        self._save_settings()

    def _start_download(self) -> None:
        urls = extract_supported_urls(self._get_url_text())
        output_dir = self.output_dir_var.get().strip().strip('"')
        custom_name = normalize_custom_name(self.name_var.get().strip())
        self.follow_active_task = True

        if not urls:
            messagebox.showerror("链接为空", "请先输入至少一个 B 站视频链接。")
            return
        if not output_dir:
            messagebox.showerror("目录为空", "请先选择视频保存目录。")
            return

        output_path = Path(output_dir).expanduser()
        output_path.mkdir(parents=True, exist_ok=True)

        if len(urls) == 1 and self.expand_collection_var.get():
            self._set_collection_parse_state()
            self._set_collection_resolving(True)
            threading.Thread(
                target=self._submit_collection_resolution,
                args=(urls[0], output_path, custom_name),
                daemon=True,
                name="resolve-collection",
            ).start()
            return

        self._enqueue_resolved_entries(
            resolved_entries=[(url, "") for url in urls],
            output_path=output_path,
            custom_name=custom_name,
            from_collection_expansion=False,
        )

    def _poll_events(self) -> None:
        processed_collection = self._drain_collection_queue(limit=1)
        processed_events = self._drain_events(limit=48)
        self._refresh_queue_summary()
        next_delay = 16 if processed_collection or processed_events else 120
        self.root.after(next_delay, self._poll_events)

    def _drain_collection_queue(self, limit: int = 1) -> int:
        processed = 0
        while processed < limit:
            try:
                event_type, payload = self.collection_queue.get_nowait()
            except queue.Empty:
                break
            processed += 1

            if event_type == "error":
                self._handle_collection_resolution_error(str(payload))
                continue

            if event_type == "resolved" and isinstance(payload, dict):
                self._enqueue_resolved_entries(
                    resolved_entries=payload["resolved_entries"],
                    output_path=payload["output_path"],
                    custom_name=payload["custom_name"],
                    from_collection_expansion=True,
                )
        return processed

    def _drain_events(self, limit: int = 48) -> int:
        processed = 0
        while processed < limit:
            try:
                event = self.event_queue.get_nowait()
            except queue.Empty:
                break
            processed += 1
            self._apply_event(event)
        return processed

    def _apply_event(self, event: TaskEvent) -> None:
        task = self.tasks.get(event.task_id)
        if task is None:
            return

        if event.title is not None:
            task.title = event.title
        if event.status is not None:
            task.status = event.status
        if event.progress_text is not None:
            task.progress_text = event.progress_text
        if event.speed_text is not None:
            task.speed_text = event.speed_text
        if event.message is not None:
            task.message = event.message
        if event.output_file is not None:
            task.output_file = event.output_file
        next_tree_text = self._format_task_list_text(task)
        if self.task_tree.item(task.task_id, "text") != next_tree_text:
            self.task_tree.item(task.task_id, text=next_tree_text)

        self._refresh_queue_summary()
        if self.follow_active_task and (event.status is None or event.status != "排队中"):
            self._show_task(task.task_id)
        elif self.selected_task_id in {None, task.task_id}:
            self._show_task(task.task_id)

    def _extract_percent(self, progress_text: str) -> float:
        match = re.search(r"(\d+(?:\.\d+)?)%", progress_text)
        if not match:
            return float(self.progress_value_var.get())
        return min(max(float(match.group(1)), 0.0), 100.0)

    def _update_status_style(self, status: str) -> None:
        if status == "完成":
            bootstyle = "success"
        elif status == "失败":
            bootstyle = "danger"
        elif status == "已跳过":
            bootstyle = "warning"
        elif status in {"下载视频", "下载视频流", "下载音频流", "合并文件", "整理文件"}:
            bootstyle = "primary"
        else:
            bootstyle = "secondary"
        self.status_badge.configure(bootstyle=bootstyle)

    def _get_url_text(self) -> str:
        return self.url_text.get("1.0", "end").strip()

    def _set_url_text(self, text: str) -> None:
        self.url_text.delete("1.0", "end")
        if text:
            self.url_text.insert("1.0", text)

    def _show_task(self, task_id: str) -> None:
        if task_id not in self.tasks:
            return
        self.selected_task_id = task_id
        self._display_task(self.tasks[task_id])

    def _display_task(self, task: DownloadTask) -> None:
        self.current_task_var.set(task.title)
        self.status_var.set(task.status)
        self.progress_text_var.set(task.progress_text)
        self.progress_value_var.set(self._extract_percent(task.progress_text))
        self.message_var.set(self._build_detail_message(task))
        self.output_path_var.set(task.output_file)
        self._update_status_style(task.status)

    def _on_task_clicked(self, _event: tk.Event) -> None:
        self.root.after_idle(self._show_selected_task)

    def _show_selected_task(self) -> None:
        selection = self.task_tree.selection()
        if not selection:
            return
        self.follow_active_task = False
        self._show_task(selection[0])

    def _refresh_queue_summary(self) -> None:
        active_count, pending_count = self.manager.get_counts()
        completed_count = sum(task.status == "完成" for task in self.tasks.values())
        skipped_count = sum(task.status == "已跳过" for task in self.tasks.values())
        failed_count = sum(task.status == "失败" for task in self.tasks.values())
        summary = (
            f"总任务 {len(self.tasks)} | 下载中 {active_count} | 排队 {pending_count} | 已完成 {completed_count}"
        )
        if skipped_count:
            summary += f" | 已跳过 {skipped_count}"
        if failed_count:
            summary += f" | 失败 {failed_count}"
        self.queue_summary_var.set(summary)

    def _save_settings(self) -> None:
        save_config(
            self.output_dir_var.get().strip(),
            1,
            self.expand_collection_var.get(),
            self.skip_existing_var.get(),
        )

    def _on_close(self) -> None:
        try:
            self._save_settings()
        finally:
            self.root.destroy()


def main() -> None:
    root = tb.Window(themename="flatly")
    VideoManagerApp(root)
    try:
        root.mainloop()
    except Exception:
        details = traceback.format_exc()
        error_log_path = write_error_log(details)
        log_hint = f"\n错误日志：{error_log_path}" if error_log_path else ""
        messagebox.showerror("程序异常", f"程序运行时发生未处理异常。{log_hint}")


if __name__ == "__main__":
    main()
